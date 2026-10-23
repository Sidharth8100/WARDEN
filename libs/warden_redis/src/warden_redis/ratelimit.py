"""Generic Cell Rate Algorithm using Redis server time and a single Lua decision."""

import math
from dataclasses import dataclass
from typing import cast

from redis.asyncio import Redis

from warden_redis.client import as_text, run_script


@dataclass(frozen=True)
class GcraDecision:
    allowed: bool
    retry_after: float


_GCRA = """
local clock = redis.call('TIME')
local now = tonumber(clock[1])*1000000 + tonumber(clock[2])
local interval = tonumber(ARGV[1])
local tat = tonumber(redis.call('GET', KEYS[1])) or now
local allow_at = tat - (tonumber(ARGV[2])-1)*interval
if now < allow_at then return {0, tostring((allow_at-now)/1000000)} end
local next_tat = math.max(tat, now) + interval
redis.call('SET', KEYS[1], string.format('%.0f', next_tat),
           'PX', math.max(1, math.ceil((next_tat-now)/1000)))
return {1, '0'}
"""


async def gcra_allow(
    redis: Redis, bucket_key: str, *, rate_per_second: float, burst: int
) -> GcraDecision:
    """burst counts the total requests admitted immediately, not extra requests.

    Shared server time prevents divergent API clocks. Expiry covers the entire
    outstanding TAT debt so an early expiry cannot replenish a busy bucket.
    """
    if not math.isfinite(rate_per_second) or rate_per_second <= 0 or burst < 1:
        raise ValueError("finite positive rate and burst >= 1 required")
    result = cast(
        list[object],
        await run_script(redis, _GCRA, [bucket_key], [1_000_000 / rate_per_second, burst]),
    )
    return GcraDecision(bool(result[0]), float(as_text(result[1])))
