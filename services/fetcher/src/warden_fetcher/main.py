"""Untrusted worker: object storage + Redis, no PostgreSQL driver or credentials."""

import asyncio
import contextlib
import os
import time
from dataclasses import replace
from datetime import UTC, datetime

import structlog

from warden_core.circuitbreaker import CircuitBreaker
from warden_core.jobs import JobPayload
from warden_fetch.orchestrate import MonitorFetchSpec, fetch
from warden_net.objectstore import ObjectStoreConfig
from warden_redis.client import as_text, component, connect
from warden_redis.coordination import heartbeat_member, list_live_members
from warden_redis.queue import (
    GROUP,
    claim_jobs,
    enqueue_job,
    move_due_retries,
    reclaim_stale,
    schedule_retry,
)
from warden_redis.worker import WorkerConfig, handle_claim, shutdown_event, wait_or_stop

_log = structlog.get_logger(__name__)


def assert_no_database_access() -> None:
    if any(key in os.environ for key in ("WARDEN_DATABASE_URL", "DATABASE_URL")):
        raise RuntimeError(
            "fetcher must never receive database credentials, including an empty URL"
        )


async def run() -> None:
    assert_no_database_access()
    config = WorkerConfig.from_environment()
    member = component(config.instance_id)
    redis = connect(config.redis_url, "fetcher")
    stop = shutdown_event()
    breaker = CircuitBreaker()
    store = ObjectStoreConfig(
        config.objectstore_endpoint,
        config.objectstore_access_key,
        config.objectstore_secret_key,
        config.objectstore_bucket,
    )
    allowlist = frozenset(filter(None, os.environ.get("WARDEN_SSRF_ALLOWLIST", "").split(",")))
    if allowlist:
        _log.warning("fetcher_explicit_test_allowlist", hosts=sorted(allowlist))

    async def fetch_job(job: JobPayload) -> None:
        retry_key = f"warden:retry:fetch:{member}"
        if not breaker.allow(job.domain, time.monotonic()):
            await schedule_retry(redis, retry_key, job, delay_s=15, attempt=job.attempt)
            return
        try:
            result = await fetch(
                MonitorFetchSpec(job.url, needs_render=job.needs_render),
                objectstore_config=store,
                extra_allowed_hosts=allowlist,
                redis=redis,
                selenium_hub_url=os.environ.get(
                    "WARDEN_SELENIUM_HUB_URL", "http://selenium-hub:4444"
                ),
            )
            if result.status >= 500 or result.status == 429:
                raise RuntimeError("transient_target_failure")
        except Exception as exc:
            breaker.record(job.domain, False, time.monotonic())
            _log.warning("fetch_attempt_failed", job_id=job.job_id, category=type(exc).__name__)
            if job.attempt + 1 < config.maximum_attempts:
                await schedule_retry(
                    redis, retry_key, job, delay_s=min(300, 2**job.attempt), attempt=job.attempt + 1
                )
                return
            outcome = replace(
                job, error="fetch_attempts_exhausted", fetched_at=datetime.now(UTC).isoformat()
            )
        else:
            breaker.record(job.domain, True, time.monotonic())
            outcome = replace(
                job,
                content_hash=result.content_hash,
                screenshot_hash=result.screenshot_hash,
                http_status=result.status,
                tier=result.tier_used,
                not_modified=result.not_modified,
                blocked_by_robots=result.blocked_by_robots,
                fetched_at=datetime.now(UTC).isoformat(),
            )
        await enqueue_job(redis, "warden:results", outcome)

    async def membership_loop() -> None:
        while not stop.is_set():
            try:
                await heartbeat_member(redis, "fetchers", member, ttl_ms=15_000)
            except Exception as exc:
                _log.warning("membership_heartbeat_failed", category=type(exc).__name__)
            await wait_or_stop(stop, 3)

    membership = asyncio.create_task(membership_loop())
    try:
        while not stop.is_set():
            try:
                live = set(await list_live_members(redis, "fetchers"))
                streams = [f"warden:jobs:{member}", "warden:jobs:ingress"]
                # Dead members' queued and pending work is explicitly adopted.
                async for raw in redis.scan_iter(match="warden:jobs:*", count=100):
                    stream = as_text(raw)
                    if stream.rsplit(":", 1)[-1] not in live and stream not in streams:
                        streams.append(stream)
                # Delay sets also outlive their member: every live worker can
                # atomically promote due work back into that stream for adoption.
                async for raw in redis.scan_iter(match="warden:retry:fetch:*", count=100):
                    key = as_text(raw)
                    target = f"warden:jobs:{key.rsplit(':', 1)[-1]}"
                    await move_due_retries(redis, key, target, now_s=time.time(), max_items=100)
                    if target not in streams and target.rsplit(":", 1)[-1] not in live:
                        streams.append(target)
                worked = False
                for stream in streams:
                    if stop.is_set():
                        break
                    claims = await reclaim_stale(
                        redis, stream, GROUP, member, idle_ms=config.idle_ms, count=1
                    )
                    if not claims:
                        claims = await claim_jobs(redis, stream, GROUP, member, count=1, block_ms=0)
                    for claim in claims:
                        worked = True
                        await handle_claim(
                            redis,
                            stream,
                            member,
                            claim,
                            idle_ms=config.idle_ms,
                            handler=fetch_job,
                            dead_stream="warden:dead:fetch",
                        )
                if not worked:
                    await wait_or_stop(stop, 0.2)
            except Exception as exc:
                _log.error("fetcher_iteration_failed", category=type(exc).__name__)
                await wait_or_stop(stop, 1)
    finally:
        membership.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await membership
        await redis.aclose()


def main() -> None:
    assert_no_database_access()
    asyncio.run(run())


if __name__ == "__main__":
    main()
