"""Admission and enqueue contract independent of deployment database credentials."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from warden_api.auth.limiter import check_rate_limit
from warden_api.errors import APIError
from warden_api.monitors.router import check_monitor
from warden_api.websocket import cursor_tuple
from warden_redis.ratelimit import GcraDecision


async def test_login_uses_shared_gcra_and_denies_with_retry_header() -> None:
    redis = MagicMock()
    with patch(
        "warden_api.auth.limiter.gcra_allow",
        new=AsyncMock(side_effect=[GcraDecision(True, 0), GcraDecision(False, 2.1)]),
    ) as allow:
        with pytest.raises(APIError) as denied:
            await check_rate_limit(redis, "127.0.0.1", "User@Example.com")
        assert denied.value.status_code == 429
        assert denied.value.headers["Retry-After"] == "3"
        assert allow.await_count == 2
        assert all(call.args[0] is redis for call in allow.await_args_list)


async def test_enqueue_is_durably_staged_before_redis_outage() -> None:
    identity = uuid.uuid4()
    monitor = SimpleNamespace(
        id=identity, url="https://example.com", domain="example.com", needs_render=False
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.return_value = monitor
    user = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=MagicMock())))
    with patch(
        "warden_api.monitors.router.enqueue_job", new=AsyncMock(side_effect=ConnectionError)
    ):
        response = await check_monitor(identity, request, session, user)
    assert response["status"] == "queued"
    assert response["monitor_id"] == str(identity)
    session.commit.assert_awaited_once()
    assert session.add.call_count == 2  # durable job ledger + dispatch outbox


@pytest.mark.parametrize("cursor", ["", "-1-0", "1", "1-2-3", "*-0", "0-x"])
def test_invalid_event_cursors_are_rejected(cursor: str) -> None:
    with pytest.raises(ValueError):
        cursor_tuple(cursor)
