"""Redis-backed access-token denylist.

Per standing project rule, Redis code is written by the human.
This file provides stub definitions with 3-level hint ladders.
"""

from redis.asyncio import Redis

# -----------------------------------------------------------------------------
# Hint Ladder for add_to_denylist:
# Level 1 (Conceptual):
#   We want to record that a specific token ID (jti) has been revoked (e.g. on
#   logout). The record should automatically disappear once the token's original
#   TTL expires because after that time, the token will be rejected anyway.
#
# Level 2 (Pseudocode):
#   key = f"denylist:{jti}"
#   if ttl_seconds > 0:
#       await redis.set(key, "1", ex=ttl_seconds)
#
# Level 3 (Redis Library Method):
#   - redis.asyncio.Redis.set(name, value, ex=seconds)
#   - Check redis-py's `set()` keyword argument `ex` for expiration in seconds.
# -----------------------------------------------------------------------------


async def add_to_denylist(redis: Redis, jti: str, ttl_seconds: int) -> None:
    """[YOU IMPLEMENT] Add jti to the denylist, expiring automatically
    after ttl_seconds (the token's own remaining lifetime — after that
    it would be rejected on exp anyway, so the denylist entry is no
    longer needed). Must be safe if called twice for the same jti
    (idempotent). No locking needed: a plain SET with an expiry is enough.
    Hint L1: which single Redis command sets a value AND an expiry atomically?
    Hint L2: redis.set(key, value, ex=ttl_seconds) — but what should `value` be
    if you never read it back for anything but existence?
    Hint L3: look at redis-py's asyncio `set()` signature; also consider
    what happens if ttl_seconds is <= 0 (a token that's already expired).
    """
    raise NotImplementedError("see docstring")


# -----------------------------------------------------------------------------
# Hint Ladder for is_denylisted:
# Level 1 (Conceptual):
#   Check whether a given token ID (jti) exists in the Redis revocation store.
#   This is a boolean existence check, not a retrieval of the value.
#
# Level 2 (Pseudocode):
#   key = f"denylist:{jti}"
#   result = await redis.exists(key)
#   return bool(result)
#
# Level 3 (Redis Library Method):
#   - redis.asyncio.Redis.exists(*names)
#   - Returns an integer count of matching keys (0 or 1).
# -----------------------------------------------------------------------------


async def is_denylisted(redis: Redis, jti: str) -> bool:
    """[YOU IMPLEMENT] Return True if jti is currently denylisted.
    Hint L1: existence check, not value check.
    Hint L2: one Redis command returns 0 or 1 for "does this key exist".
    Hint L3: EXISTS, via redis-py's `exists()`.
    """
    raise NotImplementedError("see docstring")
