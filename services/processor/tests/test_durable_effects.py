"""Real private PostgreSQL: transaction/receipt/fencing and old-result ordering.

No deployment URL, password, PEM, or existing database is accessed. The isolated
fixture uses trust auth only over a fixture-owned private Unix socket.
"""

import asyncio
import hashlib
import os
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from warden_core.jobs import JobPayload
from warden_db.dispatch import fence, stage_check
from warden_db.models import ChangeEvent, CrawlJob, Monitor, Outbox, SchedulerFence, Snapshot, User
from warden_db.session import make_engine, make_sessionmaker
from warden_net.objectstore import ObjectStoreConfig
from warden_processor.main import Processor

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    binaries = Path("/usr/lib/postgresql/16/bin")
    if not await asyncio.to_thread(binaries.is_dir):
        pytest.skip("PostgreSQL 16 binaries required for private integration fixture")
    data = tmp_path / "data"
    socket = tmp_path / "socket"
    await asyncio.to_thread(socket.mkdir)
    init = await asyncio.create_subprocess_exec(
        str(binaries / "initdb"),
        "-D",
        str(data),
        "-A",
        "trust",
        "-U",
        "test",
        "--no-locale",
        "--encoding=UTF8",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, errors = await init.communicate()
    if init.returncode:
        pytest.fail(f"private initdb failed: {errors.decode()}")
    server = await asyncio.create_subprocess_exec(
        str(binaries / "postgres"),
        "-D",
        str(data),
        "-k",
        str(socket),
        "-c",
        "listen_addresses=",
        "-p",
        "15432",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    url = f"postgresql+asyncpg://test@/postgres?host={socket}&port=15432"
    engine = make_engine(url)
    try:
        for _ in range(100):
            if server.returncode is not None:
                pytest.fail("private PostgreSQL exited during startup")
            try:
                async with engine.connect():
                    break
            except Exception:
                await asyncio.sleep(0.03)
        else:
            pytest.fail("private PostgreSQL did not start")
        # Exercise the actual migration chain, including the new fence seed.
        executable = shutil.which("alembic")
        if executable is None:
            pytest.fail("alembic missing from the declared environment")
        migration = await asyncio.create_subprocess_exec(
            executable,
            "-c",
            "libs/warden_db/alembic.ini",
            "upgrade",
            "head",
            env={**os.environ, "WARDEN_DATABASE_URL": url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await migration.communicate()
        if migration.returncode:
            pytest.fail(f"migration failed: {output.decode()}")
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()
        if server.returncode is None:
            server.terminate()
        await server.wait()


async def seed(sessions: async_sessionmaker[AsyncSession]) -> tuple[JobPayload, ObjectStoreConfig]:
    async with sessions() as session, session.begin():
        unusable = "!disabled"
        user = User(
            email=f"{uuid.uuid4().hex}@example.invalid", password_hash=unusable, plan="free"
        )
        session.add(user)
        await session.flush()
        monitor = Monitor(
            user_id=user.id,
            url="https://example.com/",
            url_canonical="https://example.com/",
            url_hash=hashlib.sha256(b"url").digest(),
            domain="example.com",
            min_interval_s=3600,
            max_interval_s=7200,
            next_due_at=datetime.now(UTC),
            needs_render=False,
            state="active",
            version=1,
        )
        session.add(monitor)
        await session.flush()
        job = stage_check(session, monitor)
    return job, ObjectStoreConfig("unused", "unused", "unused", "warden")


async def test_commit_before_ack_redelivery_and_concurrent_effects(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    job, store = await seed(sessions)
    body = b"<html><body><p>$99.99</p></body></html>"
    job = replace(job, content_hash=hashlib.sha256(body).hexdigest(), http_status=200)
    processor = Processor(sessions, store)
    with patch("warden_processor.main.get_blob", new=AsyncMock(return_value=body)):
        await asyncio.gather(*(processor.process(job) for _ in range(8)))
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 1
        receipt = await session.get(CrawlJob, uuid.UUID(job.job_id))
        assert receipt is not None and receipt.status == "completed"
        assert await session.scalar(select(func.count()).select_from(ChangeEvent)) == 0


async def test_effect_event_outbox_commit_and_failed_transaction_retry(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    first, store = await seed(sessions)
    before = b"<html><body><p>$99.99</p></body></html>"
    after = b"<html><body><p>$199.99</p></body></html>"
    blobs = {hashlib.sha256(before).hexdigest(): before, hashlib.sha256(after).hexdigest(): after}

    async def blob(config: ObjectStoreConfig, key: str) -> bytes:
        return blobs[key]

    processor = Processor(sessions, store)
    with patch("warden_processor.main.get_blob", new=blob):
        await processor.process(replace(first, content_hash=hashlib.sha256(before).hexdigest()))
        async with sessions() as session, session.begin():
            monitor = await session.get(Monitor, uuid.UUID(first.monitor_id))
            assert monitor is not None
            second = stage_check(session, monitor)
        second = replace(second, content_hash=hashlib.sha256(after).hexdigest())
        with (
            patch(
                "warden_processor.main.classify", side_effect=RuntimeError("injected_before_commit")
            ),
            pytest.raises(RuntimeError),
        ):
            await processor.process(second)
        async with sessions() as session:
            receipt = await session.get(CrawlJob, uuid.UUID(second.job_id))
            assert receipt is not None and receipt.status == "queued"
            assert await session.scalar(select(func.count()).select_from(Snapshot)) == 1
        await processor.process(second)
        await processor.process(second)  # crash-after-commit redelivery
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 2
        assert await session.scalar(select(func.count()).select_from(ChangeEvent)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(Outbox).where(Outbox.kind == "event")
            )
            == 1
        )


async def test_fencing_rejects_stale_term_globally(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session, session.begin():
        await fence(session, 10, takeover=True)
        await fence(session, 10)  # same term can write repeatedly
    async with sessions() as session, session.begin():
        await fence(session, 11, takeover=True)
    async with sessions() as session, session.begin():
        with pytest.raises(RuntimeError, match="stale"):
            await fence(session, 10)
    async with sessions() as session:
        row = await session.get(SchedulerFence, "scheduler")
        assert row is not None and row.token == 11
