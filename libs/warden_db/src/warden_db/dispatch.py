"""Transactionally stage accepted checks; relay only after the transaction commits."""

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from warden_core.jobs import JobPayload
from warden_db.models import CrawlJob, Monitor, Outbox, SchedulerFence


def stage_check(session: AsyncSession, monitor: Monitor) -> JobPayload:
    job = JobPayload(
        str(uuid.uuid4()),
        str(monitor.id),
        monitor.url,
        monitor.domain,
        needs_render=monitor.needs_render,
    )
    session.add(CrawlJob(id=uuid.UUID(job.job_id), monitor_id=monitor.id, status="queued"))
    session.add(
        Outbox(
            kind="fetch", payload=json.loads(job.encode()), idempotency_key=f"fetch:{job.job_id}"
        )
    )
    return job


async def fence(session: AsyncSession, token: int, *, takeover: bool = False) -> None:
    """Shared row lock is held through the protected transaction.

    Redis counters must persist and never be reset. Losing Redis state requires
    an explicit operator recovery; stale/lower tokens are rejected, never guessed.
    """
    row = await session.get(SchedulerFence, "scheduler", with_for_update=True)
    if row is None or token < row.token or (not takeover and token != row.token):
        raise RuntimeError("stale_scheduler_term")
    row.token = token
