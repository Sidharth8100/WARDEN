"""Authenticated, owner-scoped event replay with subscribe-first wakeups.

Pub/Sub is a wakeup signal, never the source of truth. Every wakeup and periodic
resync reads the log strictly after the delivered cursor, eliminating overlap
duplicates and recovering a missed PUBLISH without requiring a reconnect.
"""

import asyncio
import contextlib
import json
import time
import uuid
from collections import OrderedDict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette.types import Message

from warden_api.auth.dependencies import authenticate_user
from warden_api.auth.jwt import decode_access_token
from warden_api.errors import APIError
from warden_api.settings import Settings
from warden_db.models import Monitor, MonitorTag, Tag
from warden_redis.client import as_text
from warden_redis.denylist import is_denylisted
from warden_redis.eventlog import CHANNEL, STREAM, replay_since

router = APIRouter()


def cursor_tuple(cursor: str) -> tuple[int, int]:
    parts = cursor.split("-")
    if len(parts) != 2 or any(not part.isdecimal() for part in parts):
        raise ValueError("invalid stream cursor")
    return int(parts[0]), int(parts[1])


@router.websocket("/api/ws")
async def events(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.ws_allowed_origins:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    disconnect: asyncio.Task[Message] | None = None

    async def send(message: dict[str, object]) -> None:
        await asyncio.wait_for(websocket.send_json(message), 5)

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), 5)
        if len(raw) > 16_384:
            raise ValueError("subscription too large")
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise ValueError("subscription must be an object")
        token = message.get("token", "")
        cursor = message.get("cursor", "0-0")
        if not isinstance(cursor, str):
            raise ValueError("cursor must be a string")
        cursor_tuple(cursor)
        ids, tags = message.get("monitor_ids", []), message.get("tags", [])
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(ids, list)
            or not isinstance(tags, list)
            or len(ids) > 100
            or len(tags) > 20
            or any(not isinstance(identity, str) for identity in ids)
            or any(not isinstance(tag, str) for tag in tags)
        ):
            raise ValueError("invalid subscription")
        if not ids and not tags:
            raise ValueError("at least one monitor or tag is required")
        monitor_ids = {uuid.UUID(identity) for identity in ids}
        redis = websocket.app.state.redis
        async with websocket.app.state.sessionmaker() as session:
            user = await authenticate_user(token, settings, session, redis)
            user_id = str(user.id)
            authorized = set(
                await session.scalars(
                    select(Monitor.id).where(
                        Monitor.id.in_(monitor_ids),
                        Monitor.user_id == user.id,
                        Monitor.state != "deleted",
                    )
                )
            )
            if authorized != monitor_ids:
                raise ValueError("unauthorized subscription")
            if tags:
                tagged = await session.scalars(
                    select(Monitor.id)
                    .join(MonitorTag, MonitorTag.monitor_id == Monitor.id)
                    .join(Tag, Tag.id == MonitorTag.tag_id)
                    .where(
                        Tag.user_id == user.id,
                        Tag.name.in_(tags),
                        Monitor.user_id == user.id,
                        Monitor.state != "deleted",
                    )
                )
                authorized.update(tagged)
        allowed = {str(identity) for identity in authorized}
        claims = decode_access_token(token, settings)
        disconnect = asyncio.create_task(websocket.receive())
        async with redis.pubsub() as subscription:
            await subscription.subscribe(CHANNEL)
            async with asyncio.timeout(5):
                while True:
                    confirmation = await subscription.get_message(timeout=1)
                    if confirmation and confirmation["type"] == "subscribe":
                        break
            earliest = await redis.xrange(STREAM, count=1)
            if (
                earliest
                and cursor != "0-0"
                and cursor_tuple(cursor) < cursor_tuple(as_text(earliest[0][0]))
            ):
                await send({"type": "resync_required"})
                await websocket.close(code=1000)
                return
            await send({"type": "subscribed", "cursor": cursor})
            seen: OrderedDict[str, None] = OrderedDict()
            while time.time() < claims["exp"]:
                if disconnect.done():
                    if disconnect.result()["type"] != "websocket.disconnect":
                        await websocket.close(code=1008)
                    return
                if await is_denylisted(redis, claims["jti"]):
                    await websocket.close(code=1008)
                    return
                replay = await asyncio.wait_for(replay_since(redis, STREAM, cursor), 5)
                for event in replay:
                    cursor = event.cursor
                    if (
                        event.user_id == user_id
                        and event.monitor_id in allowed
                        and event.event_id not in seen
                    ):
                        await send({"type": "change", **event.as_dict()})
                        seen[event.event_id] = None
                        if len(seen) > 10_000:
                            seen.popitem(last=False)
                if replay:
                    await send({"type": "cursor", "cursor": cursor})
                if len(replay) == 500:
                    continue
                # A periodic read also repairs append-before-publish crashes.
                await subscription.get_message(ignore_subscribe_messages=True, timeout=1)
            await websocket.close(code=1008)
    except WebSocketDisconnect:
        return
    except (APIError, ValueError, TypeError, KeyError, TimeoutError):
        await websocket.close(code=1008)
    finally:
        if disconnect is not None:
            disconnect.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await disconnect
