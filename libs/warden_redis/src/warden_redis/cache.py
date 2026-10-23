"""Namespace-bounded TTL values with true, atomic LRU reads and writes."""

import hashlib

from redis.asyncio import Redis

from warden_redis.client import as_bytes, component, run_script

_PUT = """
local members = redis.call('ZRANGE', KEYS[1], 0, -1)
for _, key in ipairs(members) do
  if redis.call('EXISTS', key) == 0 then redis.call('ZREM', KEYS[1], key) end
end
redis.call('SET', KEYS[3], ARGV[1], 'EX', ARGV[2])
local seq = redis.call('INCR', KEYS[2])
redis.call('ZADD', KEYS[1], seq, KEYS[3])
local excess = redis.call('ZCARD', KEYS[1]) - tonumber(ARGV[3])
if excess > 0 then
  local evicted = redis.call('ZPOPMIN', KEYS[1], excess)
  for i=1,#evicted,2 do redis.call('DEL', evicted[i]) end
end
redis.call('EXPIRE', KEYS[1], math.max(tonumber(ARGV[2]), redis.call('TTL', KEYS[1])))
redis.call('EXPIRE', KEYS[2], math.max(tonumber(ARGV[2]), redis.call('TTL', KEYS[2])))
return 1
"""
_GET = """
local value = redis.call('GET', KEYS[3])
if not value then redis.call('ZREM', KEYS[1], KEYS[3]); return false end
local ttl = redis.call('PTTL', KEYS[3])
local seq = redis.call('INCR', KEYS[2])
redis.call('ZADD', KEYS[1], seq, KEYS[3])
if ttl > redis.call('PTTL', KEYS[1]) then redis.call('PEXPIRE', KEYS[1], ttl) end
if ttl > redis.call('PTTL', KEYS[2]) then redis.call('PEXPIRE', KEYS[2], ttl) end
return value
"""


def _keys(namespace: str, key: str) -> list[str]:
    prefix = f"warden:cache:{component(namespace)}"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return [f"{prefix}:lru", f"{prefix}:sequence", f"{prefix}:value:{digest}"]


async def cache_put(
    redis: Redis, namespace: str, key: str, value: bytes, *, ttl_seconds: int, max_entries: int
) -> None:
    """One script: purge expired index entries, SET, touch, and evict oldest.

    Monotonic sequence scores avoid clock ties. Expired ZSET members are cleaned
    at writes; value keys expire independently. Index work is O(namespace size),
    deliberately bounded by max_entries, not advertised as constant time.
    """
    if ttl_seconds < 1 or max_entries < 1:
        raise ValueError("positive TTL and namespace bound required")
    await run_script(redis, _PUT, _keys(namespace, key), [value, ttl_seconds, max_entries])


async def cache_get(redis: Redis, namespace: str, key: str) -> bytes | None:
    """GET and recency update are atomic; missing values remove stale tracking."""
    result = await run_script(redis, _GET, _keys(namespace, key), [])
    return as_bytes(result) if result is not None else None
