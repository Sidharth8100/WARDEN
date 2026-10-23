"""Fenced scheduler: PostgreSQL rebuild, monotonic ticks, bounded periodic refresh."""

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from warden_core.budget import (
    MonitorBudgetInput,
    Observation,
    estimate_change_rate,
    solve_crawl_intervals,
)
from warden_core.hashring import ConsistentHashRing
from warden_core.jobs import JobPayload
from warden_core.timingwheel import TimerHandle, TimingWheel
from warden_db.dispatch import fence, stage_check
from warden_db.models import CrawlJob, Monitor, Outbox, Plan, User
from warden_db.session import make_engine, make_sessionmaker
from warden_redis.client import connect
from warden_redis.coordination import (
    acquire_leadership,
    list_live_members,
    release_leadership,
    renew_leadership,
)
from warden_redis.queue import enqueue_job
from warden_redis.worker import shutdown_event, wait_or_stop

_log = structlog.get_logger(__name__)


class Scheduler:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], redis: Redis, token: int
    ) -> None:
        self.sessions, self.redis, self.token = sessions, redis, token
        self.wheel = TimingWheel(100, [64, 64, 64, 64])
        self.epoch = time.monotonic()
        self.handles: dict[str, TimerHandle] = {}
        self.ring = ConsistentHashRing(256)
        self.members: set[str] = set()
        self.loads: dict[str, int] = {}
        self.intervals: dict[str, float] = {}

    def schedule(self, monitor_id: str, due: datetime) -> None:
        previous = self.handles.pop(monitor_id, None)
        if previous:
            self.wheel.cancel(previous)
        delay = max(0, int((due - datetime.now(UTC)).total_seconds() * 1000))
        self.handles[monitor_id] = self.wheel.schedule(delay, monitor_id)

    async def rebuild(self) -> None:
        async with self.sessions() as session, session.begin():
            await fence(session, self.token)
            monitors = (
                await session.scalars(select(Monitor).where(Monitor.state == "active"))
            ).all()
            live = {str(monitor.id) for monitor in monitors}
            for identity in set(self.handles) - live:
                self.wheel.cancel(self.handles.pop(identity))
            for monitor in monitors:
                self.schedule(str(monitor.id), monitor.next_due_at)

    async def recompute(self) -> None:
        async with self.sessions() as session, session.begin():
            await fence(session, self.token)
            monitors = (
                await session.scalars(select(Monitor).where(Monitor.state == "active"))
            ).all()
            inputs = []
            lower = {}
            upper = {}
            for monitor in monitors:
                rows = (
                    await session.scalars(
                        select(CrawlJob)
                        .where(
                            CrawlJob.monitor_id == monitor.id,
                            CrawlJob.status == "completed",
                            CrawlJob.completed_at.is_not(None),
                        )
                        .order_by(CrawlJob.completed_at.desc())
                        .limit(100)
                    )
                ).all()
                observations = [
                    Observation((a.completed_at - b.completed_at).total_seconds(), bool(a.changed))
                    for a, b in pairwise(rows)
                    if a.completed_at and b.completed_at and a.completed_at > b.completed_at
                ]
                rate = estimate_change_rate(observations, prior_shape=2, prior_rate=3600)
                owner = await session.get(User, monitor.user_id)
                plan = await session.get(Plan, owner.plan) if owner else None
                identity = str(monitor.id)
                lower[identity] = max(monitor.min_interval_s, plan.min_interval_s if plan else 30)
                upper[identity] = max(lower[identity], monitor.max_interval_s)
                inputs.append(MonitorBudgetInput(identity, rate))
            if not inputs:
                return
            budget = float(os.environ.get("WARDEN_CRAWL_BUDGET_PER_SECOND", "1"))
            self.intervals = solve_crawl_intervals(
                inputs,
                total_budget_per_second=budget,
                min_interval_s=min(lower.values()),
                max_interval_s=max(upper.values()),
                interval_bounds={key: (lower[key], upper[key]) for key in lower},
            )
            now = datetime.now(UTC)
            for monitor in monitors:
                monitor.next_due_at = min(
                    monitor.next_due_at, now + timedelta(seconds=self.intervals[str(monitor.id)])
                )
                self.schedule(str(monitor.id), monitor.next_due_at)

    async def tick(self) -> None:
        now_ms = int((time.monotonic() - self.epoch) * 1000)
        due = self.wheel.advance(now_ms)
        for identity in due:
            self.handles.pop(identity, None)
            async with self.sessions() as session, session.begin():
                await fence(session, self.token)
                monitor = await session.get(Monitor, uuid.UUID(identity), with_for_update=True)
                if not monitor or monitor.state != "active":
                    continue
                if monitor.next_due_at <= datetime.now(UTC):
                    stage_check(session, monitor)
                    interval = self.intervals.get(identity, monitor.min_interval_s)
                    monitor.next_due_at = datetime.now(UTC) + timedelta(seconds=interval)
                self.schedule(identity, monitor.next_due_at)

    async def dispatch(self) -> None:
        members = set(await list_live_members(self.redis, "fetchers"))
        for member in self.members - members:
            self.ring.remove_member(member)
        for member in members - self.members:
            self.ring.add_member(member)
        self.members = members
        for member in members:
            previous = self.loads.get(member, 0)
            try:
                groups = await self.redis.xinfo_groups(f"warden:jobs:{member}")
            except Exception:
                # A newly joined member may not have its stream yet. If a
                # previously observed stream fails, abort rather than undercount.
                if member in self.loads:
                    raise
                groups = []
            load = sum(
                int(group.get("pending", 0)) + int(group.get("lag") or 0)
                for group in groups
                if group.get("name") in (b"workers", "workers")
            )
            self.ring.report_load(member, load - previous)
            self.loads[member] = load
        self.loads = {member: self.loads[member] for member in members}
        async with self.sessions() as session, session.begin():
            await fence(session, self.token)
            rows = (
                await session.scalars(
                    select(Outbox)
                    .where(Outbox.kind == "fetch", Outbox.status == "pending")
                    .order_by(Outbox.id)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                job = JobPayload.decode(json.dumps(row.payload).encode())
                member = self.ring.get_member(job.domain) if members else "ingress"
                await enqueue_job(self.redis, f"warden:jobs:{member}", job)
                if members:
                    self.ring.report_load(member, 1)
                    self.loads[member] += 1
                row.status = "sent"
                row.attempts += 1


async def run() -> None:
    redis = connect(os.environ["WARDEN_REDIS_URL"], "scheduler")
    engine = make_engine(os.environ["WARDEN_DATABASE_URL"])
    sessions = make_sessionmaker(engine)
    stop = shutdown_event()
    try:
        while not stop.is_set():
            holder = uuid.uuid4().hex
            token = await acquire_leadership(redis, "scheduler", holder, lease_ms=15_000)
            if token is None:
                await wait_or_stop(stop, 2)
                continue
            try:
                async with sessions() as session, session.begin():
                    await fence(session, token.value, takeover=True)
                scheduler = Scheduler(sessions, redis, token.value)
                await scheduler.rebuild()
                next_refresh = next_budget = next_renewal = time.monotonic()
                expected = time.monotonic()
                while not stop.is_set():
                    now = time.monotonic()
                    if now >= next_renewal:
                        if not await renew_leadership(redis, "scheduler", holder, lease_ms=15_000):
                            break
                        next_renewal = now + 3
                    drift = now - expected
                    if drift > 0.5:
                        _log.warning("scheduler_tick_drift", seconds=drift)
                    await scheduler.tick()
                    await scheduler.dispatch()
                    if now >= next_refresh:
                        await scheduler.rebuild()
                        next_refresh = now + 5
                    if now >= next_budget:
                        await scheduler.recompute()
                        next_budget = now + float(os.environ.get("WARDEN_BUDGET_RECOMPUTE_S", "60"))
                    expected = max(expected + 0.1, time.monotonic())
                    await wait_or_stop(stop, expected - time.monotonic())
            except Exception as exc:
                _log.error("scheduler_term_failed", category=type(exc).__name__)
                await wait_or_stop(stop, 1)
            finally:
                await release_leadership(redis, "scheduler", holder)
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
