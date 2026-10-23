"""Fenced leases and expiring membership; leases do not fence PostgreSQL themselves."""

from dataclasses import dataclass

from redis.asyncio import Redis

from warden_redis.client import as_text, component, run_script


@dataclass(frozen=True)
class FencingToken:
    value: int


_ACQUIRE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return false end
local token = redis.call('INCR', KEYS[2])
redis.call('SET', KEYS[1], ARGV[1], 'NX', 'PX', ARGV[2])
return token
"""
_RENEW = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('PEXPIRE', KEYS[1], ARGV[2])
"""
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


def _lease(role: str) -> str:
    return f"warden:coord:lease:{component(role)}"


async def acquire_leadership(
    redis: Redis, role: str, holder_id: str, *, lease_ms: int
) -> FencingToken | None:
    """Atomically acquire NX/PX and increment the never-reset fencing counter.

    holder_id must be unique per acquisition attempt/session, not a reused
    process name. The protected database must transactionally reject stale terms.
    """
    if lease_ms < 1 or not holder_id:
        raise ValueError("positive lease and nonempty holder required")
    value = await run_script(
        redis,
        _ACQUIRE,
        [_lease(role), f"warden:coord:fence:{component(role)}"],
        [holder_id, lease_ms],
    )
    return None if value is None else FencingToken(int(str(value)))


async def renew_leadership(redis: Redis, role: str, holder_id: str, *, lease_ms: int) -> bool:
    if lease_ms < 1:
        raise ValueError("positive lease required")
    return bool(await run_script(redis, _RENEW, [_lease(role)], [holder_id, lease_ms]))


async def release_leadership(redis: Redis, role: str, holder_id: str) -> None:
    await run_script(redis, _RELEASE, [_lease(role)], [holder_id])


async def heartbeat_member(redis: Redis, group: str, member_id: str, *, ttl_ms: int) -> None:
    if ttl_ms < 1:
        raise ValueError("positive heartbeat TTL required")
    await redis.set(
        f"warden:coord:members:{component(group)}:{component(member_id)}", "1", px=ttl_ms
    )


async def list_live_members(redis: Redis, group: str) -> list[str]:
    """SCAN avoids a blocking full-keyspace KEYS call.

    SCAN is weakly consistent: a pass may miss newly added members or duplicate
    entries. De-duplicate and check existence again for keys that expired during
    traversal. This is a periodically refreshed advisory view, not a consensus
    membership list. SCAN permissions do not hide other key names.
    """
    prefix = f"warden:coord:members:{component(group)}:"
    members = set()
    async for key in redis.scan_iter(match=f"{prefix}*", count=100):
        if await redis.exists(key):
            members.add(as_text(key)[len(prefix) :])
    return sorted(members)
