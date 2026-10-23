"""Retained replay plus lossy live push.

Caller ordering: confirm live subscription FIRST, then replay the durable stream,
then drain buffered live messages and deduplicate by event_id/cursor. Replaying
before subscribing leaves a gap. An old trimmed cursor requires explicit resync.
XADD precedes PUBLISH; a crash can miss live push but the outbox retries append
and publish, and reconnecting clients replay. Delivery is bounded by retention.
"""

import hashlib
from dataclasses import replace
from typing import Any, cast

from redis.asyncio import Redis

from warden_core.jobs import ChangeEventPayload
from warden_redis.client import as_bytes, as_text, run_script

STREAM = "warden:events"
CHANNEL = "warden:events:live"
_APPEND = """
local previous = redis.call('GET', KEYS[2])
if previous then return previous end
local id = redis.call('XADD', KEYS[1], 'MAXLEN', '~', ARGV[2], '*', 'payload', ARGV[1])
redis.call('SET', KEYS[2], id, 'EX', 604800)
return id
"""


async def append_event(
    redis: Redis, stream_key: str, event: ChangeEventPayload, *, max_len_approx: int
) -> str:
    """Deduplicate retried outbox appends for seven days; publish is repeatable.

    Consumers also deduplicate event_id beyond that horizon. Retention must be
    sized for recovery requirements, not treated as infinite durability.
    """
    if max_len_approx < 1:
        raise ValueError("positive retention required")
    digest = hashlib.sha256(event.event_id.encode()).hexdigest()
    cursor = as_text(
        await run_script(
            redis,
            _APPEND,
            [stream_key, f"warden:eventids:{digest}"],
            [event.encode(), max_len_approx],
        )
    )
    await redis.publish(CHANNEL, replace(event, cursor=cursor).encode())
    return cursor


async def replay_since(redis: Redis, stream_key: str, cursor: str) -> list[ChangeEventPayload]:
    """One bounded page strictly after cursor; caller advances to the last cursor.

    The caller validates retention before replay and bounds any subscription
    buffer. Clients must authenticate and authorize monitor/tag ownership.
    """
    result: list[ChangeEventPayload] = []
    entries = cast(list[Any], await redis.xrange(stream_key, min=f"({cursor}", count=500))
    for message_id, fields in entries:
        cursor = as_text(message_id)
        event = ChangeEventPayload.decode(as_bytes(fields.get(b"payload", fields.get("payload"))))
        result.append(replace(event, cursor=cursor))
    return result
