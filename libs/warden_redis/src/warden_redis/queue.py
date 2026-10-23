"""At-least-once streams; no trimming of job streams or unacknowledged work.

Acknowledgment is a durable handoff boundary, not a deletion policy. Broken
payloads are returned as inspectable claims so callers can quarantine them
durably before ack. Reclaims page across calls, including empty scan pages.
"""

import math
from dataclasses import dataclass
from typing import Any, cast
from weakref import WeakKeyDictionary

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from warden_core.jobs import JobPayload
from warden_redis.client import as_bytes, as_text, run_script

GROUP = "workers"
_cursors: WeakKeyDictionary[Redis, dict[tuple[str, str, str], str]] = WeakKeyDictionary()


@dataclass(frozen=True)
class ClaimedJob:
    message_id: str
    job: JobPayload | None
    raw: bytes


def _decode(entries: list[Any]) -> list[ClaimedJob]:
    result = []
    for message_id, fields in entries:
        raw = as_bytes(fields.get(b"payload", fields.get("payload", b"")))
        try:
            job = JobPayload.decode(raw)
        except (ValueError, TypeError, OverflowError):
            job = None
        result.append(ClaimedJob(as_text(message_id), job, raw))
    return result


async def ensure_group(redis: Redis, stream_key: str, group: str = GROUP) -> None:
    try:
        await redis.xgroup_create(stream_key, group, id="0", mkstream=True)
    except ResponseError as exc:
        if not str(exc).startswith("BUSYGROUP"):
            raise


async def enqueue_job(redis: Redis, stream_key: str, job: JobPayload) -> str:
    await ensure_group(redis, stream_key)
    return as_text(await redis.xadd(stream_key, {"payload": job.encode()}))


async def claim_jobs(
    redis: Redis, stream_key: str, group: str, consumer: str, *, count: int, block_ms: int
) -> list[ClaimedJob]:
    """Only new entries via >. block_ms=0 means nonblocking, never Redis BLOCK 0."""
    if count < 1 or block_ms < 0:
        raise ValueError("positive count and nonnegative block required")
    await ensure_group(redis, stream_key, group)
    response = cast(
        list[Any],
        await redis.xreadgroup(
            group, consumer, {stream_key: ">"}, count=count, block=block_ms or None
        ),
    )
    return [job for _, entries in response for job in _decode(entries)]


async def ack_job(redis: Redis, stream_key: str, group: str, message_id: str) -> None:
    await redis.xack(stream_key, group, message_id)


async def reclaim_stale(
    redis: Redis, stream_key: str, group: str, consumer: str, *, idle_ms: int, count: int
) -> list[ClaimedJob]:
    if idle_ms < 1 or count < 1:
        raise ValueError("positive idle threshold and count required")
    await ensure_group(redis, stream_key, group)
    cursors = _cursors.setdefault(redis, {})
    key = (stream_key, group, consumer)
    result = await redis.xautoclaim(
        stream_key, group, consumer, idle_ms, start_id=cursors.get(key, "0-0"), count=count
    )
    cursors[key] = as_text(result[0])
    jobs = _decode(result[1])
    if jobs:
        await redis.incrby("warden:metrics:reclaimed", len(jobs))
    return jobs


_RETRY = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2])/1000000
redis.call('ZADD', KEYS[1], now+tonumber(ARGV[2]), ARGV[1])
return 1
"""
_MOVE = """
local entries = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
for _, value in ipairs(entries) do
  redis.call('XADD', KEYS[2], '*', 'payload', value)
  redis.call('ZREM', KEYS[1], value)
end
if #entries > 0 then redis.call('INCRBY', KEYS[3], #entries) end
return #entries
"""


async def schedule_retry(
    redis: Redis, retry_zset_key: str, job: JobPayload, *, delay_s: float, attempt: int
) -> None:
    if not math.isfinite(delay_s) or delay_s < 0 or attempt < 0:
        raise ValueError("finite nonnegative delay/attempt required")
    await run_script(redis, _RETRY, [retry_zset_key], [job.with_attempt(attempt).encode(), delay_s])


async def move_due_retries(
    redis: Redis, retry_zset_key: str, stream_key: str, *, now_s: float, max_items: int
) -> int:
    """XADD before ZREM, inside one script: prefer duplicates over permanent loss.

    Interleaving is prevented by Lua. A runtime/permission error after XADD does
    not roll back that write: the original ZSET member remains recoverable.
    Re-running may duplicate, which durable processor receipts absorb. No XTRIM.
    """
    if not math.isfinite(now_s) or max_items < 1:
        raise ValueError("finite timestamp and positive count required")
    await ensure_group(redis, stream_key)
    return cast(
        int,
        await run_script(
            redis, _MOVE, [retry_zset_key, stream_key, "warden:metrics:retries"], [now_s, max_items]
        ),
    )


_TOUCH = """
local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1)
if #pending == 0 or pending[1][2] ~= ARGV[2] then return 0 end
redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[2], 0, ARGV[3], 'IDLE', 0, 'JUSTID')
return 1
"""


async def renew_claim(
    redis: Redis, stream: str, group: str, consumer: str, message_id: str
) -> bool:
    """Owner-checked claim heartbeat prevents slow live work from false reclaim."""
    return bool(await run_script(redis, _TOUCH, [stream], [group, consumer, message_id]))
