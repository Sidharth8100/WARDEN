"""Real PostgreSQL classification→rule→delivery with deterministic crash boundaries."""

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from warden_alerter.receiver import Receiver
from warden_alerter.relay import Outcome, Relay
from warden_rules.language import compile_rule

from warden_core.jobs import JobPayload
from warden_db.dispatch import stage_check
from warden_db.learning import promote
from warden_db.models import (
    AlertEndpoint,
    ChangeEvent,
    CrawlJob,
    Delivery,
    DeliveryAttempt,
    LearningExample,
    ModelAlias,
    ModelVersion,
    Monitor,
    MonitorTag,
    Outbox,
    RuleEvaluation,
    RuleRevision,
    RuleState,
    User,
)
from warden_db.session import make_engine, make_sessionmaker
from warden_net.objectstore import ObjectStoreConfig
from warden_net.webhook import HttpResult, safe_post
from warden_processor.main import Processor

pytestmark = pytest.mark.integration
FILE = str(Path(__file__).resolve())
KEY = b"fixture-only-not-a-deployment-key-123456"
SOURCE = (
    "when price.drop_percent >= 10 and price.current < 1000 and count(changes, 24h) < 3 "
    'then webhook("fixture") + tag("bargain") cooldown 6h'
)


async def setup(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[Processor, JobPayload, dict[str, bytes]]:
    async with sessions() as session, session.begin():
        disabled_hash = "!disabled"
        key_reference = "WARDEN_WEBHOOK_KEY_FIXTURE"
        user = User(
            email=f"{uuid.uuid4().hex}@example.invalid", password_hash=disabled_hash, plan="free"
        )
        session.add(user)
        await session.flush()
        monitor = Monitor(
            user_id=user.id,
            url="https://example.org/item",
            url_canonical="https://example.org/item",
            url_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
            domain="example.org",
            min_interval_s=3600,
            max_interval_s=7200,
            next_due_at=datetime.now(UTC),
            needs_render=False,
            state="active",
            version=1,
            rule_src=SOURCE,
            selector_config={"price_css": ".price", "price_currency": "USD"},
        )
        session.add(monitor)
        await session.flush()
        session.add(
            AlertEndpoint(
                user_id=user.id,
                name="fixture",
                kind="webhook",
                target="https://alerts.example.org/hook",
                secret_ref=key_reference,
            )
        )
        session.add(
            RuleRevision(
                monitor_id=monitor.id,
                user_id=user.id,
                version=1,
                source=SOURCE,
                digest=compile_rule(SOURCE).digest,
            )
        )
        first = stage_check(session, monitor)
    bodies = [
        b'<html><body><span class="price">$100.00</span></body></html>',
        b'<html><body><span class="price">$89.00</span></body></html>',
        b'<html><body><span class="price">$79.00</span></body></html>',
    ]
    blobs = {hashlib.sha256(x).hexdigest(): x for x in bodies}
    redis = AsyncMock(spec=Redis)
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    processor = Processor(
        sessions, ObjectStoreConfig("unused", "unused", "unused", "fixture"), redis
    )
    return processor, first, blobs


async def classified_change(sessions: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, int]:
    processor, first, blobs = await setup(sessions)
    keys = list(blobs)

    async def read(config: ObjectStoreConfig, key: str) -> bytes:
        return blobs[key]

    with patch("warden_processor.main.get_blob", new=read):
        await processor.process(replace(first, content_hash=keys[0]))
        async with sessions() as session, session.begin():
            monitor = await session.get(Monitor, uuid.UUID(first.monitor_id))
            second = stage_check(session, monitor)
        await processor.process(replace(second, content_hash=keys[1]))
        await processor.process(replace(second, content_hash=keys[1]))
    async with sessions() as session:
        delivery = await session.scalar(select(Delivery))
        assert delivery is not None
        return delivery.id, delivery.event_id


def receiver_post(receiver: Receiver):
    async def post(url: str, body: bytes, headers: dict[str, str]) -> HttpResult:
        def send(
            url: str, address: str, data: bytes, supplied: dict[str, str], timeout: float
        ) -> HttpResult:
            return HttpResult(receiver.accept({k.lower(): v for k, v in supplied.items()}, data))

        with (
            patch(
                "warden_net.ssrf_check.resolve_host",
                new=AsyncMock(return_value=[ip_address("93.184.216.34")]),
            ),
            patch("warden_net.webhook._post_pinned", new=send),
        ):
            return await safe_post(url, body, headers)

    return post


async def test_classification_rule_atomic_intent_cooldown_and_tag(pg_sessions) -> None:
    identity, event_id = await classified_change(pg_sessions)
    async with pg_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Delivery)) == 1
        assert await session.scalar(select(func.count()).select_from(MonitorTag)) == 1
        event = await session.get(ChangeEvent, event_id)
        assert event.extracted["fields"]["price.drop_percent"] == "11"
        record = await session.scalar(select(RuleEvaluation))
        assert record.decision == "matched"
        state = await session.scalar(select(RuleState))
        assert state.next_allowed_at - event.detected_at == timedelta(hours=6)
        delivery = await session.get(Delivery, identity)
        outbox = await session.get(Outbox, delivery.outbox_id)
        assert outbox.status == "pending" and outbox.kind == "alert.webhook"


async def test_redis_failure_rolls_back_event_intents_and_cooldown(pg_sessions) -> None:
    processor, first, blobs = await setup(pg_sessions)
    keys = list(blobs)

    async def read(config, key):
        return blobs[key]

    with patch("warden_processor.main.get_blob", new=read):
        await processor.process(replace(first, content_hash=keys[0]))
        async with pg_sessions() as session, session.begin():
            second = stage_check(session, await session.get(Monitor, uuid.UUID(first.monitor_id)))
        processor.redis.get.side_effect = ConnectionError
        with pytest.raises(ConnectionError):
            await processor.process(replace(second, content_hash=keys[1]))
        async with pg_sessions() as session:
            assert await session.scalar(select(func.count()).select_from(ChangeEvent)) == 0
            assert await session.scalar(select(func.count()).select_from(Delivery)) == 0
            assert await session.scalar(select(func.count()).select_from(RuleState)) == 0
            assert (await session.get(CrawlJob, uuid.UUID(second.job_id))).status == "queued"
        processor.redis.get.side_effect = None
        await processor.process(replace(second, content_hash=keys[1]))
    async with pg_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Delivery)) == 1


async def test_two_relays_and_stale_completion_do_not_overwrite(
    pg_sessions, tmp_path: Path
) -> None:
    identity, _ = await classified_change(pg_sessions)
    receiver = Receiver(tmp_path / "receiver.sqlite", {"v1": KEY})
    first = Relay(pg_sessions, post=receiver_post(receiver), key_provider=lambda ref: KEY)
    claims = await asyncio.gather(first.claim(), first.claim())
    claimed = [c for c in claims if c]
    assert len(claimed) == 1
    claim = claimed[0]
    assert (await first.dispatch(claim)).success
    assert await first.finish(claim, Outcome(True))
    assert not await first.finish(claim, Outcome(False, True))
    async with pg_sessions() as session:
        assert (await session.get(Delivery, identity)).status == "delivered"


def killed_relay(url: str, database: str, marker: str) -> None:
    async def child():
        engine = make_engine(url)
        try:
            relay = Relay(
                make_sessionmaker(engine),
                post=receiver_post(Receiver(Path(database), {"v1": KEY})),
                key_provider=lambda ref: KEY,
                lease_s=0.3,
            )
            claim = await relay.claim()
            if claim is None or not (await relay.dispatch(claim)).success:
                raise RuntimeError("fixture_dispatch_failed")
            await asyncio.to_thread(Path(marker).write_text, str(claim.identity))
            await asyncio.Event().wait()  # parent kills after receiver commit, before finish
        finally:
            await engine.dispose()

    asyncio.run(child())


async def test_kill_after_receiver_commit_restarts_without_duplicate_effect(
    pg_sessions, tmp_path: Path
) -> None:
    identity, event_id = await classified_change(pg_sessions)
    database, marker = tmp_path / "receiver.sqlite", tmp_path / "receiver-committed"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        FILE,
        str(pg_sessions.kw["bind"].url),
        str(database),
        str(marker),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        for _ in range(300):
            if marker.exists():
                break
            if process.returncode is not None:
                pytest.fail("relay died before receiver commit marker")
            await asyncio.sleep(0.02)
        assert marker.exists()
        process.kill()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode is not None and process.returncode < 0
        await asyncio.sleep(0.4)
        relay = Relay(
            pg_sessions,
            post=receiver_post(Receiver(database, {"v1": KEY})),
            key_provider=lambda ref: KEY,
        )
        claim = await relay.claim()
        assert claim is not None and claim.identity == identity and claim.number == 2
        assert await relay.finish(claim, await relay.dispatch(claim))
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM effects").fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1
        async with pg_sessions() as session:
            statuses = (
                await session.scalars(
                    select(DeliveryAttempt.status).order_by(DeliveryAttempt.number)
                )
            ).all()
            assert statuses == ["abandoned", "succeeded"]
            assert (await session.get(Delivery, identity)).status == "delivered"
        output = os.environ.get("WARDEN_DELIVERY_PROOF_OUTPUT")
        if output:
            await asyncio.to_thread(
                Path(output).write_text,
                json.dumps(
                    {
                        "event_id": event_id,
                        "delivery_id": str(identity),
                        "relay_sigkill_after_receiver_commit": True,
                        "attempts": 2,
                        "receiver_effects": 1,
                        "database": "real private PostgreSQL",
                        "signatures_verified": True,
                        "transport": (
                            "injected pinned transport; real SSRF guard with fixture public DNS"
                        ),
                        "public_dns_tls_wire_proof": False,
                    },
                    indent=2,
                )
                + "\n",
            )
    finally:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.wait(), 5)


async def test_terminal_http_and_retry_breaker_dead_letters(pg_sessions) -> None:
    identity, _ = await classified_change(pg_sessions)
    relay = Relay(pg_sessions)
    claim = await relay.claim()
    assert claim
    assert await relay.finish(claim, Outcome(False, category="http_rejected", http_status=403))
    async with pg_sessions() as session:
        delivery = await session.get(Delivery, identity)
        assert delivery.status == "dead"
        assert (await session.get(Outbox, delivery.outbox_id)).status == "failed"


async def test_promotion_gate_and_rollback_use_owned_frozen_holdout(pg_sessions) -> None:
    _, event_id = await classified_change(pg_sessions)
    async with pg_sessions() as session, session.begin():
        event = await session.get(ChangeEvent, event_id)
        monitor = await session.get(Monitor, event.monitor_id)
        owner = monitor.user_id
        # Deliberately bad candidate predicts negative on an evenly split holdout.
        bad = ModelVersion(user_id=owner, weights={"0": -100}, prior_weights={}, samples=1)
        session.add(bad)
        await session.flush()
        session.add(ModelAlias(scope=str(owner), shadow_id=bad.id))
        for index in range(40):
            row = ChangeEvent(
                monitor_id=monitor.id,
                to_snapshot_id=event.to_snapshot_id,
                detected_at=datetime.now(UTC),
                kind="fixture",
                meaningful=index % 2 == 0,
                classifier_version="static-v2",
                features={},
            )
            session.add(row)
            await session.flush()
            session.add(
                LearningExample(
                    event_id=row.id,
                    user_id=owner,
                    label=index % 2 == 0,
                    features={"direction": 1 if index % 2 == 0 else -1},
                    split="holdout",
                    static_prediction=index % 2 == 0,
                )
            )
    async with pg_sessions() as session, session.begin():
        result = await promote(session, owner)
        assert result["allowed"] is False
        assert (await session.get(ModelAlias, str(owner))).production_id is None
        assert not (
            await session.scalars(
                select(LearningExample).where(LearningExample.trained_at.is_not(None))
            )
        ).all()


if __name__ == "__main__":
    killed_relay(*sys.argv[1:])


async def test_real_acl_history_boundaries_flush_and_poison_repair(
    pg_sessions, private_redis_roles
) -> None:
    from warden_processor.rules import RedisHistory

    _, event_id = await classified_change(pg_sessions)
    redis = private_redis_roles["processor"]
    async with pg_sessions() as session, session.begin():
        first = await session.get(ChangeEvent, event_id)
        now = first.detected_at
        events = []
        for seconds in (86401, 86400, 1, 0):
            row = ChangeEvent(
                monitor_id=first.monitor_id,
                to_snapshot_id=first.to_snapshot_id,
                detected_at=now - timedelta(seconds=seconds),
                kind="fixture",
                meaningful=True,
                classifier_version="static-v2",
                features={},
            )
            session.add(row)
            await session.flush()
            events.append(row)
        current = ChangeEvent(
            monitor_id=first.monitor_id,
            to_snapshot_id=first.to_snapshot_id,
            detected_at=now,
            kind="fixture",
            meaningful=True,
            classifier_version="static-v2",
            features={},
        )
        session.add(current)
        await session.flush()
        history = RedisHistory(session, redis, current)
        # Closed [t-window,t] boundary, current event excluded; first event at t included.
        assert await history.count(86400) == 4
        assert await history.count(1) == 3
        key = f"warden:eventids:rule-history:{first.monitor_id}"
        await private_redis_roles["testadmin"].delete(key)
        assert await history.count(86400) == 4
        await redis.set(key, '{"watermark":999,"events":[[1,"NaN"]]}')
        assert await history.count(86400) == 4
        assert await redis.get(key) is not None


async def test_owner_api_redelivery_idempotence_and_readonly_replay(pg_sessions) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from warden_api.auth.dependencies import get_current_user
    from warden_api.deps import get_db
    from warden_api.errors import register_error_handlers
    from warden_api.rules.router import router

    identity, event_id = await classified_change(pg_sessions)
    async with pg_sessions() as session, session.begin():
        row = await session.get(Delivery, identity)
        owner = await session.get(User, row.user_id)
        monitor_id = (await session.get(ChangeEvent, event_id)).monitor_id
        disabled = "!disabled"
        other = User(email="other@example.invalid", password_hash=disabled)
        session.add(other)
        row.status = "dead"
        (await session.get(Outbox, row.outbox_id)).status = "failed"
    selected = [other]
    app = FastAPI()
    app.include_router(router)
    register_error_handlers(app)

    async def user():
        return selected[0]

    async def database():
        async with pg_sessions() as session:
            yield session

    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/deliveries")).json()["items"] == []
        assert (await client.get(f"/api/deliveries/{identity}/attempts")).status_code == 404
        payload = {"monitor_id": str(monitor_id), "source": SOURCE}
        assert (await client.post("/api/rules/dry-run", json=payload)).status_code == 404
        selected[0] = owner
        before = (await client.get("/api/deliveries")).json()
        replay = await client.post("/api/rules/dry-run", json=payload)
        assert replay.status_code == 200 and replay.json()["writes_actions_or_cooldown"] is False
        assert (await client.get("/api/deliveries")).json() == before
        with patch("warden_api.rules.router.validate_destination", new=AsyncMock()):
            headers = {"Idempotency-Key": "restore-fixture-1"}
            first = await client.post(f"/api/deliveries/{identity}/redeliver", headers=headers)
            again = await client.post(f"/api/deliveries/{identity}/redeliver", headers=headers)
        assert first.status_code == 202 and again.json() == first.json()
    async with pg_sessions() as session:
        row = await session.get(Delivery, identity)
        assert row.retry_limit == 16 and row.attempts == 0
        assert await session.scalar(select(func.count()).select_from(Delivery)) == 1


async def test_measured_real_query_plans_on_representative_fixture(pg_sessions) -> None:
    import statistics

    from sqlalchemy import text

    _, event_id = await classified_change(pg_sessions)
    async with pg_sessions() as session, session.begin():
        event = await session.get(ChangeEvent, event_id)
        monitor = await session.get(Monitor, event.monitor_id)
        params = {"monitor": monitor.id, "owner": monitor.user_id, "at": datetime.now(UTC)}
        await session.execute(
            text("""
            INSERT INTO crawl_jobs (id,monitor_id,status,created_at,completed_at)
            SELECT md5('query-proof-'||i)::uuid, :monitor,
              CASE WHEN i % 10000 = 0 THEN 'queued' ELSE 'completed' END,
              now() - (i||' seconds')::interval,
              CASE WHEN i % 10000 = 0 THEN NULL
                   ELSE now() - (i||' seconds')::interval + interval '2 seconds' END
            FROM generate_series(1,150000) i
        """),
            params,
        )
        await session.execute(
            text("""
            INSERT INTO change_events(monitor_id,to_snapshot_id,kind,meaningful,features)
            SELECT :monitor, 1, 'query-fixture', i % 2 = 0, '{}'::jsonb
            FROM generate_series(1,30000) i
        """),
            params,
        )
        await session.execute(
            text("""
            INSERT INTO learning_examples(event_id,user_id,label,features,split,static_prediction)
            SELECT id,:owner, id % 2 = 0,
                   jsonb_build_object('filler',repeat('x',1024)),
                   CASE WHEN id % 50 = 0 THEN 'holdout' ELSE 'train' END, id % 2 = 0
            FROM change_events WHERE kind='query-fixture'
        """),
            params,
        )
    # Fixture-owned indexes only. Never run this benchmark against deployment data.
    async with pg_sessions.kw["bind"].connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        definitions = {
            "ix_crawl_jobs_created_slo": (
                "CREATE INDEX ix_crawl_jobs_created_slo ON crawl_jobs(created_at) "
                "INCLUDE(status,completed_at)"
            ),
            "ix_crawl_jobs_queued_age": (
                "CREATE INDEX ix_crawl_jobs_queued_age ON crawl_jobs(created_at) "
                "WHERE status='queued'"
            ),
            "ix_learning_owner_split_event": (
                "CREATE INDEX ix_learning_owner_split_event ON "
                "learning_examples(user_id,split,event_id)"
            ),
        }
        for name in definitions:
            await connection.execute(text(f"DROP INDEX {name}"))
        await connection.execute(text("VACUUM ANALYZE crawl_jobs"))
        await connection.execute(text("VACUUM ANALYZE learning_examples"))
        queries = {
            "durable_queued_age": (
                "SELECT count(*),min(created_at) FROM crawl_jobs WHERE status='queued'"
            ),
            "slo_5m_population": (
                "SELECT count(*) FROM crawl_jobs "
                "WHERE created_at >= CAST(:at AS timestamptz) - interval '5 minutes' "
                "AND created_at <= CAST(:at AS timestamptz) - interval '120 seconds'"
            ),
            "promotion_holdout": (
                "SELECT * FROM learning_examples "
                "WHERE user_id=:owner AND split='holdout' ORDER BY event_id"
            ),
        }

        async def measure():
            measured = {}
            for name, query in queries.items():
                plans = []
                for _ in range(5):
                    raw = await connection.scalar(
                        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query), params
                    )
                    plans.append(json.loads(raw)[0] if isinstance(raw, str) else raw[0])
                measured[name] = {
                    "sql": query,
                    "median_execution_ms": statistics.median(p["Execution Time"] for p in plans),
                    "plan": plans[-1],
                }
            return measured

        before = await measure()
        for sql in definitions.values():
            await connection.execute(text(sql))
        after = await measure()
        for name in queries:
            assert (
                after[name]["plan"]["Plan"]["Actual Rows"]
                == before[name]["plan"]["Plan"]["Actual Rows"]
            )
        output = os.environ.get("WARDEN_QUERY_PROOF_OUTPUT")
        if output:
            await asyncio.to_thread(
                Path(output).write_text,
                json.dumps(
                    {
                        "scope": "real PostgreSQL 16, fixture-owned data; not production timings",
                        "crawl_rows": 150002,
                        "learning_rows": 30000,
                        "selection": (
                            "three instrumented application hot-path queries; "
                            "not an exhaustive production slow-query ranking"
                        ),
                        "repetitions": 5,
                        "cache_policy": "warm-cache before and after; no OS cache manipulation",
                        "before": before,
                        "after": after,
                    },
                    indent=2,
                    default=str,
                )
                + "\n",
            )


async def test_real_streaming_replica_replays_and_rejects_writes(pg_sessions, tmp_path) -> None:
    import shutil

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    binaries = Path("/usr/lib/postgresql/16/bin")
    primary_url = pg_sessions.kw["bind"].url
    data, socket = tmp_path / "standby", tmp_path / "standby-socket"
    await asyncio.to_thread(socket.mkdir)
    backup = await asyncio.create_subprocess_exec(
        str(binaries / "pg_basebackup"),
        "-D",
        str(data),
        "-h",
        primary_url.query["host"],
        "-p",
        "15432",
        "-U",
        "test",
        "-R",
        "--wal-method=stream",
        "--checkpoint=fast",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, errors = await backup.communicate()
    assert backup.returncode == 0, errors.decode()
    process = await asyncio.create_subprocess_exec(
        str(binaries / "postgres"),
        "-D",
        str(data),
        "-k",
        str(socket),
        "-p",
        "15433",
        "-c",
        "listen_addresses=",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    engine = make_engine(f"postgresql+asyncpg://test@/postgres?host={socket}&port=15433")
    try:
        for _ in range(200):
            try:
                async with engine.connect() as connection:
                    assert await connection.scalar(text("SELECT pg_is_in_recovery()"))
                break
            except (OSError, DBAPIError):
                await asyncio.sleep(0.03)
        else:
            pytest.fail("standby did not start")
        async with pg_sessions() as session, session.begin():
            session.add(
                User(email="replica-marker@example.invalid", password_hash="!" + "disabled")
            )
        async with pg_sessions() as session:
            lsn = await session.scalar(text("SELECT pg_current_wal_lsn()::text"))
        for _ in range(200):
            async with engine.connect() as connection:
                ready = await connection.scalar(
                    text("SELECT pg_last_wal_replay_lsn() >= CAST(CAST(:lsn AS text) AS pg_lsn)"),
                    {"lsn": lsn},
                )
                if ready:
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT count(*) FROM users "
                                "WHERE email='replica-marker@example.invalid'"
                            )
                        )
                        == 1
                    )
                    replay = await connection.scalar(text("SELECT pg_last_wal_replay_lsn()::text"))
                    break
            await asyncio.sleep(0.03)
        else:
            pytest.fail("replica did not replay committed marker")
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError, match="read-only"):
                await connection.execute(text("UPDATE users SET plan='free'"))
        output = os.environ.get("WARDEN_REPLICA_PROOF_OUTPUT")
        if output:
            await asyncio.to_thread(
                Path(output).write_text,
                json.dumps(
                    {
                        "scope": (
                            "real PostgreSQL 16 primary and streaming standby; private Unix sockets"
                        ),
                        "base_backup": True,
                        "in_recovery": True,
                        "committed_marker_visible": True,
                        "write_rejected": True,
                        "primary_lsn": lsn,
                        "replica_replay_lsn": replay,
                        "deployment_compose_credentials_provisioned": False,
                    },
                    indent=2,
                )
                + "\n",
            )
    finally:
        await engine.dispose()
        if process.returncode is None:
            process.terminate()
        await process.wait()
        await asyncio.to_thread(shutil.rmtree, data)


async def test_live_mailpit_delivery_is_logged(pg_sessions, monkeypatch) -> None:
    import httpx

    host = os.environ.get("WARDEN_TEST_MAILPIT_HOST")
    if not host:
        pytest.skip("explicit existing Mailpit address required for live SMTP proof")
    identity, _ = await classified_change(pg_sessions)
    async with pg_sessions() as session, session.begin():
        delivery = await session.get(Delivery, identity)
        delivery.kind, delivery.destination = "email", "fixture@example.invalid"
        endpoint = await session.get(AlertEndpoint, delivery.endpoint_id)
        endpoint.kind, endpoint.target = "email", delivery.destination
        (await session.get(Outbox, delivery.outbox_id)).kind = "alert.email"
    monkeypatch.setenv("WARDEN_SMTP_HOST", host)
    monkeypatch.setenv("WARDEN_SMTP_PORT", "1025")
    relay = Relay(pg_sessions)
    assert await relay.once()
    async with httpx.AsyncClient(base_url=f"http://{host}:8025", trust_env=False) as client:
        found = await client.get(
            "/api/v1/search", params={"query": f"message-id:{identity}@warden.invalid"}
        )
        assert found.status_code == 200
        assert found.json()["messages_count"] == 1
        assert len(found.json()["messages"]) == 1
    async with pg_sessions() as session:
        row = await session.get(Delivery, identity)
        assert row.status == "delivered" and row.attempts == 1
    output = os.environ.get("WARDEN_MAILPIT_PROOF_OUTPUT")
    if output:
        await asyncio.to_thread(
            Path(output).write_text,
            json.dumps(
                {
                    "scope": (
                        "real existing Docker Mailpit via SMTP; private PostgreSQL delivery ledger"
                    ),
                    "delivery_id": str(identity),
                    "message_id_matches": True,
                    "mailpit_matches": 1,
                    "status": "delivered",
                    "attempts": 1,
                    "external_email_sent": False,
                },
                indent=2,
            )
            + "\n",
        )


async def test_good_promotion_shadow_and_one_command_rollback(pg_sessions) -> None:
    from warden_core.learning import OnlineLogistic
    from warden_db.learning import alias, rollback, score_observation
    from warden_db.models import ShadowScore

    _, event_id = await classified_change(pg_sessions)
    model = OnlineLogistic()
    for i in range(200):
        model.update({"direction": 1 if i % 2 else -1}, bool(i % 2))
    async with pg_sessions() as session, session.begin():
        first = await session.get(ChangeEvent, event_id)
        monitor = await session.get(Monitor, first.monitor_id)
        owner = monitor.user_id
        candidate = ModelVersion(
            user_id=owner, weights=model.serialized(), prior_weights={}, samples=200
        )
        session.add(candidate)
        await session.flush()
        pointer = await alias(session, str(owner), lock=True)
        pointer.shadow_id = candidate.id
        await score_observation(session, first, owner)
        assert first.classifier_version != "online:" + str(candidate.id)
        await session.flush()
        assert await session.scalar(select(func.count()).select_from(ShadowScore)) == 1
        for i in range(40):
            row = ChangeEvent(
                monitor_id=monitor.id,
                to_snapshot_id=first.to_snapshot_id,
                kind="gate-fixture",
                meaningful=True,
                features={},
            )
            session.add(row)
            await session.flush()
            session.add(
                LearningExample(
                    event_id=row.id,
                    user_id=owner,
                    label=bool(i % 2),
                    features={"direction": 1 if i % 2 else -1},
                    split="holdout",
                    static_prediction=True,
                )
            )
    async with pg_sessions() as session, session.begin():
        result = await promote(session, owner)
        assert result["allowed"] is True
        pointer = await session.get(ModelAlias, str(owner))
        production = pointer.production_id
        assert production == candidate.id
        # The same model cannot claim a new improvement over itself.
        assert (await promote(session, owner))["allowed"] is False
        assert pointer.production_id == production
        await rollback(session, owner)
        assert pointer.production_id is None and pointer.previous_id == production
        assert await session.get(ModelVersion, production) is not None


async def test_window_projection_has_constant_space_beyond_ten_thousand_events(
    pg_sessions, private_redis_roles
) -> None:
    from sqlalchemy import text

    from warden_processor.rules import RedisHistory

    _, event_id = await classified_change(pg_sessions)
    async with pg_sessions() as session, session.begin():
        first = await session.get(ChangeEvent, event_id)
        await session.execute(
            text("""
            INSERT INTO change_events(monitor_id,to_snapshot_id,detected_at,kind,meaningful)
            SELECT :monitor, 1, :stamp, 'history-fixture', true
            FROM generate_series(1,10001)
        """),
            {"monitor": first.monitor_id, "stamp": first.detected_at},
        )
        current = ChangeEvent(
            monitor_id=first.monitor_id,
            to_snapshot_id=1,
            detected_at=first.detected_at + timedelta(seconds=1),
            kind="current",
            meaningful=True,
        )
        session.add(current)
        await session.flush()
        redis = private_redis_roles["processor"]
        history = RedisHistory(session, redis, current)
        assert await history.count(86400) == 10002
        assert await history.count(86400) == 10002
        raw = await redis.get(f"warden:eventids:rule-history:{first.monitor_id}")
        assert raw and len(raw) < 1024
        projection = json.loads(raw)
        assert projection["counts"]["86400"] == 10002
        assert "events" not in projection
