"""Tests for student-implemented Redis token denylist functions.

All tests are tagged @pytest.mark.student_todo and are excluded from the default test run.
Run them with: make check-todos (or uv run pytest -q -m student_todo).
"""

import asyncio
import os
import uuid

import pytest
from redis.asyncio import Redis

from warden_api.security.denylist import add_to_denylist, is_denylisted


@pytest.fixture
async def redis_client():
    """Connect to the local dev Redis instance on port 6379."""
    url = os.environ.get("WARDEN_TEST_REDIS_API_URL")
    if not url:
        pytest.skip("authenticated WARDEN_TEST_REDIS_API_URL required")
    client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis not available on 127.0.0.1:6379")
    yield client
    await client.aclose()


@pytest.mark.student_todo
@pytest.mark.asyncio
async def test_denylist_add_then_check_is_true(redis_client: Redis):  # type: ignore[type-arg]
    """Verify adding a JTI to denylist makes is_denylisted return True."""
    jti = f"test-jti-{uuid.uuid4().hex}"
    await add_to_denylist(redis_client, jti, ttl_seconds=60)
    assert await is_denylisted(redis_client, jti) is True


@pytest.mark.student_todo
@pytest.mark.asyncio
async def test_denylist_never_added_is_false(redis_client: Redis):  # type: ignore[type-arg]
    """Verify an unknown JTI returns False."""
    jti = f"never-added-{uuid.uuid4().hex}"
    assert await is_denylisted(redis_client, jti) is False


@pytest.mark.student_todo
@pytest.mark.asyncio
async def test_denylist_ttl_expires(redis_client: Redis):  # type: ignore[type-arg]
    """Verify that a denylisted token automatically expires after its TTL."""
    jti = f"expiring-jti-{uuid.uuid4().hex}"
    # Add with a 1 second TTL
    await add_to_denylist(redis_client, jti, ttl_seconds=1)
    assert await is_denylisted(redis_client, jti) is True

    # Sleep slightly past expiration
    await asyncio.sleep(1.2)
    assert await is_denylisted(redis_client, jti) is False


@pytest.mark.student_todo
@pytest.mark.asyncio
async def test_denylist_logout_integration_rejects_token(client, auth_tokens):
    """Verify logout adds the access token's JTI to denylist so that subsequent
    requests fail with 401.
    """
    access_token = auth_tokens["access_token"]
    csrf_token = auth_tokens["csrf_token"]

    # 1. Protected endpoint works before logout
    headers = {"Authorization": f"Bearer {access_token}"}
    res = await client.get("/api/monitors", headers=headers)
    assert res.status_code == 200

    logout_headers = {
        "Authorization": f"Bearer {access_token}",
        "X-CSRF-Token": csrf_token,
    }
    logout_cookies = {
        "warden_csrf": csrf_token,
        "warden_refresh": auth_tokens["refresh_token"],
    }
    logout_res = await client.post(
        "/api/auth/logout", headers=logout_headers, cookies=logout_cookies
    )
    assert logout_res.status_code == 200

    # 3. Request with the same access token must now be rejected
    rejected_res = await client.get("/api/monitors", headers=headers)
    assert rejected_res.status_code == 401
