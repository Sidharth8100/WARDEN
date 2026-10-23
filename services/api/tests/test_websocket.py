"""Gateway ordering, authorization and cursor recovery using explicit test doubles."""

import asyncio
import time
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from warden_api.websocket import router
from warden_core.jobs import ChangeEventPayload


def gateway(authorized: bool = True, earliest: bytes | None = None):
    identity, owner = uuid.uuid4(), uuid.uuid4()
    order = []
    session = AsyncMock()
    session.scalars.return_value = [identity] if authorized else []
    sessions = MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    sessions.return_value.__aexit__ = AsyncMock(return_value=False)

    class Subscription:
        confirmed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def subscribe(self, channel):
            order.append("subscribe")

        async def get_message(self, **kwargs):
            if not self.confirmed:
                self.confirmed = True
                order.append("confirm")
                return {"type": "subscribe"}
            await asyncio.sleep(0.01)
            return None

    redis = MagicMock()
    redis.pubsub.return_value = Subscription()
    redis.xrange = AsyncMock(return_value=[(earliest, {})] if earliest else [])
    redis.exists = AsyncMock(return_value=0)
    app = FastAPI()
    app.include_router(router)
    app.state.settings = SimpleNamespace(ws_allowed_origins=("http://localhost:18000",))
    app.state.redis, app.state.sessionmaker = redis, sessions
    return app, identity, owner, order


def test_gateway_subscribes_confirms_then_replays_and_filters_other_owners() -> None:
    app, monitor, owner, order = gateway()
    event = ChangeEventPayload("1", str(monitor), str(owner), "now", "text", 0.8, "10-0")
    count = 0

    async def replay(*args):
        nonlocal count
        order.append("replay")
        count += 1
        return (
            [replace(event, user_id=str(uuid.uuid4()), cursor="9-0"), event] if count == 1 else []
        )

    with (
        patch(
            "warden_api.websocket.authenticate_user",
            new=AsyncMock(return_value=SimpleNamespace(id=owner)),
        ),
        patch(
            "warden_api.websocket.decode_access_token",
            return_value={"exp": time.time() + 60, "jti": "id"},
        ),
        patch("warden_api.websocket.replay_since", new=replay),
        TestClient(app) as client,
        client.websocket_connect("/api/ws") as socket,
    ):
        socket.send_json({"token": "test-token", "monitor_ids": [str(monitor)], "cursor": "0-0"})
        assert socket.receive_json()["type"] == "subscribed"
        assert socket.receive_json()["event_id"] == "1"
        assert socket.receive_json() == {"type": "cursor", "cursor": "10-0"}
    assert order[:3] == ["subscribe", "confirm", "replay"]


def test_other_owner_subscription_is_rejected_before_live_subscribe() -> None:
    app, monitor, owner, order = gateway(authorized=False)
    with (
        patch(
            "warden_api.websocket.authenticate_user",
            new=AsyncMock(return_value=SimpleNamespace(id=owner)),
        ),
        TestClient(app) as client,
        client.websocket_connect("/api/ws") as socket,
    ):
        socket.send_json({"token": "test-token", "monitor_ids": [str(monitor)]})
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008
    assert order == []


def test_trimmed_cursor_requires_explicit_resync() -> None:
    app, monitor, owner, _ = gateway(earliest=b"20-0")
    with (
        patch(
            "warden_api.websocket.authenticate_user",
            new=AsyncMock(return_value=SimpleNamespace(id=owner)),
        ),
        patch(
            "warden_api.websocket.decode_access_token",
            return_value={"exp": time.time() + 60, "jti": "id"},
        ),
        TestClient(app) as client,
        client.websocket_connect("/api/ws") as socket,
    ):
        socket.send_json({"token": "test-token", "monitor_ids": [str(monitor)], "cursor": "10-0"})
        assert socket.receive_json() == {"type": "resync_required"}


def test_browser_origin_is_checked_before_acceptance() -> None:
    app, _, _, _ = gateway()
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/ws", headers={"origin": "https://hostile.example"}),
    ):
        pytest.fail("cross-origin socket must not be accepted")


@pytest.mark.parametrize(
    "payload", [[], None, {"token": "test", "monitor_ids": "bad"}, {"cursor": 3}]
)
def test_malformed_subscriptions_close_cleanly(payload) -> None:
    app, _, _, _ = gateway()
    with TestClient(app) as client, client.websocket_connect("/api/ws") as socket:
        socket.send_json(payload)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008
