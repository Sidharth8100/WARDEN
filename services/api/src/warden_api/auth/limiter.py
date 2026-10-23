"""Shared Redis admission; no process-local login counters remain."""

import hashlib
import math

from redis.asyncio import Redis

from warden_api.errors import APIError
from warden_redis.ratelimit import gcra_allow


async def check_rate_limit(redis: Redis, ip: str | None, email: str | None) -> None:
    """Limit attempts by IP and normalized account; success does not reset debt."""
    for kind, value in (("ip", ip), ("email", email.lower().strip() if email else None)):
        if value is None:
            continue
        digest = hashlib.sha256(value.encode()).hexdigest()
        decision = await gcra_allow(
            redis, f"warden:rate:login:{kind}:{digest}", rate_per_second=5 / 300, burst=5
        )
        if not decision.allowed:
            delay = max(1, math.ceil(decision.retry_after))
            raise APIError(
                status_code=429,
                title="Too Many Requests",
                detail="Too many login attempts",
                headers={"Retry-After": str(delay)},
            )
