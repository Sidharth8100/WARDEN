"""Shared worker lifecycle; no service or database imports."""

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Self

import structlog
from redis.asyncio import Redis

from warden_core.jobs import JobPayload
from warden_redis.queue import GROUP, ClaimedJob, ack_job, renew_claim

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WorkerConfig:
    redis_url: str = field(repr=False)
    instance_id: str
    idle_ms: int = 60_000
    maximum_attempts: int = 8
    objectstore_endpoint: str = "http://objectstore:8333"
    objectstore_bucket: str = "warden"
    objectstore_access_key: str = field(default="", repr=False)
    objectstore_secret_key: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls) -> Self:
        import uuid

        return cls(
            redis_url=os.environ["WARDEN_REDIS_URL"],
            instance_id=os.environ.get("WARDEN_INSTANCE_ID", uuid.uuid4().hex),
            idle_ms=int(os.environ.get("WARDEN_CLAIM_IDLE_MS", "60000")),
            maximum_attempts=int(os.environ.get("WARDEN_MAXIMUM_ATTEMPTS", "8")),
            objectstore_endpoint=os.environ.get(
                "WARDEN_OBJECTSTORE_ENDPOINT", "http://objectstore:8333"
            ),
            objectstore_bucket=os.environ.get("WARDEN_OBJECTSTORE_BUCKET", "warden"),
            objectstore_access_key=os.environ.get("WARDEN_OBJECTSTORE_ACCESS_KEY", ""),
            objectstore_secret_key=os.environ.get("WARDEN_OBJECTSTORE_SECRET_KEY", ""),
        )


def shutdown_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    return stop


async def wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=max(0.001, seconds))


async def handle_claim(
    redis: Redis,
    stream: str,
    consumer: str,
    claim: ClaimedJob,
    *,
    idle_ms: int,
    handler: Callable[[JobPayload], Awaitable[None]],
    dead_stream: str,
) -> None:
    """Keep live claims fresh; ack only after handler's durable handoff returns.

    Any handler failure leaves its claim pending. Unknown payloads are copied to
    an inspectable dead stream before acknowledgment. Never log raw job bodies,
    target URLs, connection errors, or credentials. Ownership loss is observable;
    durable receipts protect a duplicate result if the old worker still completes.
    """
    if claim.job is None:
        await redis.xadd(
            dead_stream, {"payload": claim.raw, "source": stream, "message_id": claim.message_id}
        )
        await ack_job(redis, stream, GROUP, claim.message_id)
        return

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(max(0.01, idle_ms / 3000))
            if not await renew_claim(redis, stream, GROUP, consumer, claim.message_id):
                _log.warning("claim_ownership_lost", job_id=claim.job.job_id if claim.job else "")
                return

    task = asyncio.create_task(heartbeat())
    started = time.monotonic()
    try:
        await handler(claim.job)
        await ack_job(redis, stream, GROUP, claim.message_id)
        _log.info("claim_done", job_id=claim.job.job_id, elapsed_s=time.monotonic() - started)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
