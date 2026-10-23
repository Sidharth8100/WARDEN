"""Shared token revocation; absent or expired records do not revoke a JWT."""

from redis.asyncio import Redis


async def add_to_denylist(redis: Redis, jti: str, ttl_seconds: int) -> None:
    if ttl_seconds > 0:
        await redis.set(f"denylist:{jti}", "1", ex=ttl_seconds)


async def is_denylisted(redis: Redis, jti: str) -> bool:
    return bool(await redis.exists(f"denylist:{jti}"))
