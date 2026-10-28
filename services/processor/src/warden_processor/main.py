"""Trusted result processing: monitor lock + receipt + effects + outbox, one transaction."""

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from warden_observe.telemetry import configure, span
from warden_rules.context import build_context, serialize
from warden_rules.evaluator import Value

from warden_compare.classify import (
    CLASSIFIER_VERSION,
    classify,
    extract_features,
    load_classifier_config,
)
from warden_compare.extract import extract_price
from warden_compare.normalize import normalize
from warden_core.bloomfilter import BloomFilter
from warden_core.jobs import ChangeEventPayload, JobPayload
from warden_db.learning import score_observation
from warden_db.models import ChangeEvent, CrawlJob, Monitor, Outbox, Snapshot
from warden_db.session import make_engine, make_sessionmaker
from warden_net.objectstore import BlobNotFoundError, ObjectStoreConfig, get_blob
from warden_processor.rules import evaluate_event
from warden_redis.client import connect
from warden_redis.eventlog import STREAM, append_event
from warden_redis.queue import GROUP, claim_jobs, reclaim_stale
from warden_redis.worker import WorkerConfig, handle_claim, shutdown_event, wait_or_stop

_log = structlog.get_logger(__name__)


class Processor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        store: ObjectStoreConfig,
        redis: Redis | None = None,
    ) -> None:
        self.sessions = sessions
        self.store = store
        self.redis = redis
        self._seen = BloomFilter(100_000, 0.01)

    async def process(self, job: JobPayload) -> None:
        with span("processor.compare_and_commit", job.traceparent):
            await self._process(job)

    async def _process(self, job: JobPayload) -> None:
        """Serialize monitor history and receipt; reject forged/mismatched job references.

        Duplicate deliveries return without effects, including a crash after commit
        and before queue ack. Raw snapshots remain immutable claim-check keyframes;
        a read-only processor never uploads blobs. The codec is independently tested.
        """
        monitor_id, job_id = uuid.UUID(job.monitor_id), uuid.UUID(job.job_id)
        async with self.sessions() as session, session.begin():
            monitor = await session.scalar(
                select(Monitor).where(Monitor.id == monitor_id).with_for_update()
            )
            record = await session.get(CrawlJob, job_id, with_for_update=True)
            if (
                not monitor
                or not record
                or record.monitor_id != monitor_id
                or job.url != monitor.url
            ):
                raise ValueError("unrecognized_or_mismatched_job")
            if record.status != "queued":
                return
            now = datetime.now(UTC)
            record.completed_at = now
            if job.error:
                record.status, record.error = "failed", job.error
                record.changed = False
                return
            record.status, record.changed = "completed", False
            if not job.content_hash or job.not_modified or job.blocked_by_robots:
                return
            body = await get_blob(self.store, job.content_hash)
            digest = hashlib.sha256(body).digest()
            if digest.hex() != job.content_hash:
                raise ValueError("object_hash_mismatch")
            previous = await session.scalar(
                select(Snapshot)
                .where(Snapshot.monitor_id == monitor.id)
                .order_by(Snapshot.fetched_at.desc(), Snapshot.id.desc())
                .limit(1)
            )
            key = f"{monitor.id}:{job.content_hash}".encode()
            unchanged = (
                key in self._seen and previous is not None and previous.content_hash == digest
            )
            classification = None
            rule_context: dict[str, Value] | None = None
            if previous is not None and not unchanged:
                old_body = await get_blob(self.store, previous.blob_ref or "")
                if old_body != body:
                    old = normalize(old_body, selector_config=monitor.selector_config)
                    new = normalize(body, selector_config=monitor.selector_config)
                    before_image = (
                        await get_blob(self.store, previous.screenshot_ref)
                        if previous.screenshot_ref
                        else None
                    )
                    after_image = (
                        await get_blob(self.store, job.screenshot_hash)
                        if job.screenshot_hash
                        else None
                    )
                    features = extract_features(old, new, before_image, after_image)
                    weights, threshold = load_classifier_config()
                    classification = classify(features, weights, threshold)
                    record.changed = old.text != new.text
                    configuration = monitor.selector_config or {}
                    currency = configuration.get("price_currency")
                    rule_context = build_context(
                        kind="structural",
                        score=classification.score,
                        meaningful=classification.meaningful,
                        before=old.text,
                        after=new.text,
                        previous_price=extract_price(old, configuration),
                        current_price=extract_price(new, configuration),
                        currency=currency if isinstance(currency, str) else None,
                    )
            snapshot = Snapshot(
                monitor_id=monitor.id,
                fetched_at=now,
                kind="keyframe",
                content_hash=digest,
                blob_ref=job.content_hash,
                screenshot_ref=job.screenshot_hash,
                http_status=job.http_status,
                size_bytes=len(body),
                tier=job.tier,
            )
            session.add(snapshot)
            await session.flush()
            record.snapshot_id = snapshot.id
            if classification and previous is not None and rule_context is not None:
                event = ChangeEvent(
                    monitor_id=monitor.id,
                    from_snapshot_id=previous.id,
                    to_snapshot_id=snapshot.id,
                    detected_at=now,
                    kind="structural",
                    score=classification.score,
                    meaningful=classification.meaningful,
                    features={
                        **classification.features,
                        "static_prediction": classification.meaningful,
                    },
                    extracted=serialize(rule_context),
                    classifier_version=CLASSIFIER_VERSION,
                )
                session.add(event)
                await session.flush()
                record.event_id = event.id
                await score_observation(session, event, monitor.user_id)
                rule_context["event.meaningful"] = event.meaningful
                rule_context["event.score"] = (
                    Decimal(str(event.score)) if event.score is not None else None
                )
                event.extracted = serialize(rule_context)
                if event.meaningful and monitor.rule_src and self.redis is not None:
                    await evaluate_event(session, self.redis, monitor, event, rule_context)
                payload = ChangeEventPayload(
                    str(event.id),
                    str(monitor.id),
                    str(monitor.user_id),
                    now.isoformat(),
                    event.kind,
                    event.score,
                    meaningful=event.meaningful,
                )
                session.add(
                    Outbox(
                        kind="event",
                        payload=json.loads(payload.encode()),
                        idempotency_key=f"event:{event.id}",
                    )
                )
            self._seen.add(key)

    async def handle(self, job: JobPayload, redis: Redis) -> None:
        """Quarantine permanently invalid results; leave transient failures pending."""
        try:
            await self.process(job)
        except ValueError as exc:
            async with self.sessions() as session, session.begin():
                record = await session.get(CrawlJob, uuid.UUID(job.job_id), with_for_update=True)
                if (
                    record
                    and str(record.monitor_id) == job.monitor_id
                    and record.status == "queued"
                ):
                    record.status = "failed"
                    record.error = (
                        "missing_snapshot_blob"
                        if isinstance(exc, BlobNotFoundError)
                        else "invalid_result"
                    )
                    record.completed_at = datetime.now(UTC)
            await redis.xadd(
                "warden:dead:processor", {"payload": job.encode(), "reason": type(exc).__name__}
            )


async def publish_pending(sessions: async_sessionmaker[AsyncSession], config: WorkerConfig) -> int:
    redis = connect(config.redis_url, "processor")
    try:
        async with sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(Outbox)
                    .where(Outbox.kind == "event", Outbox.status == "pending")
                    .order_by(Outbox.id)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                event = ChangeEventPayload.decode(json.dumps(row.payload).encode())
                await append_event(redis, STREAM, event, max_len_approx=100_000)
                row.status = "sent"
                row.attempts += 1
            return len(rows)
    finally:
        await redis.aclose()


async def run() -> None:
    configure("warden-processor")
    config = WorkerConfig.from_environment()
    redis = connect(config.redis_url, "processor")
    engine = make_engine(os.environ["WARDEN_DATABASE_URL"])
    sessions = make_sessionmaker(engine)
    stop = shutdown_event()
    processor = Processor(
        sessions,
        ObjectStoreConfig(
            config.objectstore_endpoint,
            config.objectstore_access_key,
            config.objectstore_secret_key,
            config.objectstore_bucket,
        ),
        redis,
    )

    async def process(job: JobPayload) -> None:
        await processor.handle(job, redis)

    try:
        while not stop.is_set():
            try:
                claims = await reclaim_stale(
                    redis,
                    "warden:results",
                    GROUP,
                    config.instance_id,
                    idle_ms=config.idle_ms,
                    count=1,
                )
                if not claims:
                    claims = await claim_jobs(
                        redis, "warden:results", GROUP, config.instance_id, count=1, block_ms=200
                    )
                for claim in claims:
                    await handle_claim(
                        redis,
                        "warden:results",
                        config.instance_id,
                        claim,
                        idle_ms=config.idle_ms,
                        handler=process,
                        dead_stream="warden:dead:processor",
                    )
                await publish_pending(sessions, config)
            except Exception as exc:
                _log.error("processor_iteration_failed", category=type(exc).__name__)
                await wait_or_stop(stop, 1)
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
