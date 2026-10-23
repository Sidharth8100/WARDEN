"""Real Lua/Streams/ACL tests, including a partial-script crash boundary."""

import asyncio
import time
import uuid
from dataclasses import replace

import pytest
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from warden_core.jobs import ChangeEventPayload, JobPayload
from warden_redis.cache import cache_get, cache_put
from warden_redis.client import connect
from warden_redis.coordination import (
    acquire_leadership,
    heartbeat_member,
    list_live_members,
    release_leadership,
    renew_leadership,
)
from warden_redis.denylist import add_to_denylist, is_denylisted
from warden_redis.eventlog import CHANNEL, STREAM, append_event, replay_since
from warden_redis.queue import (
    _MOVE,
    GROUP,
    ack_job,
    claim_jobs,
    enqueue_job,
    move_due_retries,
    reclaim_stale,
    renew_claim,
    schedule_retry,
)
from warden_redis.ratelimit import gcra_allow

pytestmark = pytest.mark.integration


def job() -> JobPayload:
    return JobPayload(str(uuid.uuid4()), str(uuid.uuid4()), "https://example.com/", "example.com")


def test_named_identity_required() -> None:
    for url, identity in [
        ("redis://localhost", "api"),
        ("redis://default:p@localhost", "default"),
        ("redis://fetcher:p@localhost", "api"),
    ]:
        with pytest.raises(ValueError):
            connect(url, identity)


async def test_acl_positive_negative_and_denylist(redis_roles: dict[str, Redis]) -> None:
    api, fetcher = redis_roles["api"], redis_roles["fetcher"]
    await add_to_denylist(api, "revoked", 60)
    assert await is_denylisted(api, "revoked")
    assert not await is_denylisted(api, "absent")
    for client, key in [(api, "warden:jobs:ingress"), (fetcher, "denylist:revoked")]:
        with pytest.raises(ResponseError):
            await client.set(key, "forbidden")
    with pytest.raises(ResponseError):
        await fetcher.get("warden:coord:lease:scheduler")
    with pytest.raises(ResponseError):
        await api.publish(CHANNEL, "forbidden")


async def test_atomic_lru_under_concurrency_and_read_recency(redis_roles: dict[str, Redis]) -> None:
    redis = redis_roles["fetcher"]
    await cache_put(redis, "robots", "old", b"\xff", ttl_seconds=10, max_entries=2)
    await cache_put(redis, "robots", "new", b"new", ttl_seconds=10, max_entries=2)
    assert await cache_get(redis, "robots", "old") == b"\xff"
    await cache_put(redis, "robots", "third", b"third", ttl_seconds=10, max_entries=2)
    assert await cache_get(redis, "robots", "new") is None
    assert await cache_get(redis, "robots", "old") == b"\xff"

    async def put(index: int) -> None:
        await cache_put(redis, "robots", str(index), b"value", ttl_seconds=10, max_entries=16)
        assert await redis_roles["testadmin"].zcard("warden:cache:robots:lru") <= 16
        await cache_get(redis, "robots", str(index))

    await asyncio.gather(*(put(i) for i in range(1000)))


async def test_ttl_expiry(redis_roles: dict[str, Redis]) -> None:
    await cache_put(redis_roles["fetcher"], "robots", "short", b"x", ttl_seconds=1, max_entries=2)
    await add_to_denylist(redis_roles["api"], "short", 1)
    await asyncio.sleep(1.05)
    assert await cache_get(redis_roles["fetcher"], "robots", "short") is None
    assert not await is_denylisted(redis_roles["api"], "short")


async def test_gcra_simultaneous_callers(redis_roles: dict[str, Redis]) -> None:
    api = redis_roles["api"]
    results = await asyncio.gather(
        *(gcra_allow(api, "warden:rate:login", rate_per_second=0.1, burst=7) for _ in range(200))
    )
    assert sum(result.allowed for result in results) == 7
    assert all(0 < result.retry_after <= 10 for result in results if not result.allowed)


async def test_lease_trace_and_membership(redis_roles: dict[str, Redis]) -> None:
    redis = redis_roles["scheduler"]
    a, other = await asyncio.gather(
        acquire_leadership(redis, "scheduler", "A", lease_ms=100),
        acquire_leadership(redis, "scheduler", "B", lease_ms=100),
    )
    assert (a is None) != (other is None)
    first = a or other
    await asyncio.sleep(0.13)
    second = await acquire_leadership(redis, "scheduler", "C", lease_ms=1000)
    assert first is not None and second is not None and second.value > first.value
    assert not await renew_leadership(redis, "scheduler", "A", lease_ms=1000)
    await release_leadership(redis, "scheduler", "A")
    assert await renew_leadership(redis, "scheduler", "C", lease_ms=1000)
    fetcher = redis_roles["fetcher"]
    await asyncio.gather(
        *(heartbeat_member(fetcher, "fetchers", str(i), ttl_ms=100) for i in range(30))
    )
    assert len(await list_live_members(redis, "fetchers")) == 30
    await asyncio.sleep(0.13)
    assert await list_live_members(redis, "fetchers") == []


async def test_queue_lifecycle_reclaim_and_owner_heartbeat(redis_roles: dict[str, Redis]) -> None:
    redis = redis_roles["fetcher"]
    stream = "warden:jobs:worker"
    payload = job()
    await enqueue_job(redis, stream, payload)
    claims = await claim_jobs(redis, stream, GROUP, "dead", count=1, block_ms=0)
    assert claims[0].job == payload
    assert await renew_claim(redis, stream, GROUP, "dead", claims[0].message_id)
    assert not await renew_claim(redis, stream, GROUP, "stranger", claims[0].message_id)
    await asyncio.sleep(0.06)
    reclaimed = await reclaim_stale(redis, stream, GROUP, "live", idle_ms=30, count=1)
    assert reclaimed[0].message_id == claims[0].message_id
    assert not await renew_claim(redis, stream, GROUP, "dead", claims[0].message_id)
    await ack_job(redis, stream, GROUP, reclaimed[0].message_id)
    pending = await redis_roles["testadmin"].xpending(stream, GROUP)
    assert pending["pending"] == 0


async def test_retry_roundtrip_and_no_loss_on_partial_script(redis_roles: dict[str, Redis]) -> None:
    redis, admin = redis_roles["fetcher"], redis_roles["testadmin"]
    payload = job()
    stream, retries = "warden:jobs:worker", "warden:retry:fetch:worker"
    await schedule_retry(redis, retries, payload, delay_s=0, attempt=1)
    await enqueue_job(redis, stream, job())
    # Remove only ZREM permission from an isolated fixture identity. Redis scripts
    # are atomic against interleaving, but runtime errors do not undo prior XADD.
    await admin.execute_command(
        "ACL", "SETUSER", "fault", "on", "nopass", "~*", "+eval", "+zrangebyscore", "+xadd"
    )
    fault = Redis(
        unix_socket_path=redis.connection_pool.connection_kwargs["path"],
        username="fault",
        password="",
    )
    try:
        with pytest.raises(ResponseError):
            await fault.eval(_MOVE, 2, retries, stream, time.time() + 1, 100)
    finally:
        await fault.aclose()
    assert await admin.zcard(retries) == 1
    assert await move_due_retries(redis, retries, stream, now_s=time.time() + 1, max_items=100) == 1
    claims = await claim_jobs(redis, stream, GROUP, "live", count=10, block_ms=0)
    matching = [claim for claim in claims if claim.job and claim.job.job_id == payload.job_id]
    assert matching and all(claim.job == replace(payload, attempt=1) for claim in matching)
    assert await admin.zcard(retries) == 0


async def test_live_claim_heartbeat_prevents_premature_reclaim(
    redis_roles: dict[str, Redis],
) -> None:
    redis = redis_roles["fetcher"]
    stream = "warden:jobs:slow"
    await enqueue_job(redis, stream, job())
    await enqueue_job(redis, stream, job())
    claim, control = await claim_jobs(redis, stream, GROUP, "slow", count=2, block_ms=0)
    await asyncio.sleep(0.04)
    assert await renew_claim(redis, stream, GROUP, "slow", claim.message_id)
    reclaimed = await reclaim_stale(redis, stream, GROUP, "other", idle_ms=30, count=10)
    assert [entry.message_id for entry in reclaimed] == [control.message_id]
    await ack_job(redis, stream, GROUP, control.message_id)
    await asyncio.sleep(0.04)
    reclaimed = await reclaim_stale(redis, stream, GROUP, "other", idle_ms=30, count=10)
    assert [entry.message_id for entry in reclaimed] == [claim.message_id]


async def test_event_subscribe_first_replay_overlap_dedup(redis_roles: dict[str, Redis]) -> None:
    processor, api = redis_roles["processor"], redis_roles["api"]
    event = ChangeEventPayload("event-1", str(uuid.uuid4()), str(uuid.uuid4()), "now", "text", 0.8)
    first = await append_event(processor, STREAM, event, max_len_approx=1000)
    async with api.pubsub() as pubsub:
        await pubsub.subscribe(CHANNEL)
        while True:
            notice = await pubsub.get_message(timeout=1)
            if notice and notice["type"] == "subscribe":
                break
        second = replace(event, event_id="event-2")
        cursor = await append_event(processor, STREAM, second, max_len_approx=1000)
        replay = await replay_since(api, STREAM, first)
        notice = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        assert notice is not None
        live = ChangeEventPayload.decode(notice["data"])
        unique = {item.event_id: item for item in [*replay, live]}
        assert list(unique) == ["event-2"]
        assert unique["event-2"].cursor == cursor
        assert await append_event(processor, STREAM, second, max_len_approx=1000) == cursor
        assert len(await replay_since(api, STREAM, "0-0")) == 2


async def test_replay_pages_are_bounded_and_cursor_covers_backlog(
    redis_roles: dict[str, Redis],
) -> None:
    processor, api = redis_roles["processor"], redis_roles["api"]
    event = ChangeEventPayload("base", str(uuid.uuid4()), str(uuid.uuid4()), "now", "text", 0.8)
    for index in range(523):
        await append_event(
            processor, STREAM, replace(event, event_id=str(index)), max_len_approx=1000
        )
    first = await replay_since(api, STREAM, "0-0")
    assert len(first) == 500
    second = await replay_since(api, STREAM, first[-1].cursor)
    assert len(second) == 23
    assert [e.event_id for e in first + second] == [str(index) for index in range(523)]
