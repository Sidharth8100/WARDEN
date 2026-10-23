"""Integration tests for POST /api/monitors/{id}/check endpoint and synchronous pipeline.

Covers:
1. First check stores baseline keyframe snapshot and returns 204 No Content.
2. Subsequent check without meaningful change returns 204 No Content.
3. Subsequent check with meaningful price change returns 200 OK with ChangeEvent.
4. Cross-user isolation: 404 Not Found when checking another user's monitor.
5. Non-existent monitor: 404 Not Found.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden_api.auth.passwords import hash_password
from warden_api.auth.tokens import create_new_refresh_session
from warden_api.settings import get_settings
from warden_db.models import Snapshot, User


@pytest.mark.asyncio
async def test_check_monitor_not_found(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
) -> None:
    """Non-existent monitor returns 404 Not Found."""
    fake_id = uuid.uuid4()
    res = await client.post(
        f"/api/monitors/{fake_id}/check",
        headers=auth_tokens["headers"],
    )
    assert res.status_code == 404
    data = res.json()
    assert data["title"] == "Not Found"


@pytest.mark.asyncio
async def test_check_monitor_cross_user_isolation(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Checking another user's monitor returns 404 Not Found (prevents enumeration)."""
    # Create monitor as user A
    create_res = await client.post(
        "/api/monitors",
        json={"url": "https://example.com/check-iso"},
        headers=auth_tokens["headers"],
    )
    assert create_res.status_code == 201
    monitor_id = create_res.json()["id"]

    # Create user B
    user_b = User(
        email=f"user-b-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("UserBPassword123!"),
        role="user",
        plan="free",
    )
    db_session.add(user_b)
    await db_session.commit()
    await db_session.refresh(user_b)

    settings = get_settings()
    _, _, token_b = await create_new_refresh_session(db_session, user_b, settings)
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Attempt check as user B
    res = await client.post(
        f"/api/monitors/{monitor_id}/check",
        headers=headers_b,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_first_check_accepts_durable_job_without_inline_snapshot(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    from warden_db.models import CrawlJob

    create = await client.post(
        "/api/monitors",
        json={"url": "https://example.com/first-check"},
        headers=auth_tokens["headers"],
    )
    assert create.status_code == 201
    monitor_id = uuid.UUID(create.json()["id"])
    with patch("warden_api.monitors.router.enqueue_job", new_callable=AsyncMock) as enqueue:
        response = await client.post(
            f"/api/monitors/{monitor_id}/check", headers=auth_tokens["headers"]
        )
    assert response.status_code == 202
    job_id = uuid.UUID(response.json()["job_id"])
    assert await db_session.get(CrawlJob, job_id) is not None
    enqueue.assert_awaited_once()
    snapshots = (
        await db_session.scalars(select(Snapshot).where(Snapshot.monitor_id == monitor_id))
    ).all()
    assert len(snapshots) == 0


@pytest.mark.asyncio
async def test_accepted_check_survives_redis_outage_via_outbox(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    from warden_db.models import Outbox

    create = await client.post(
        "/api/monitors",
        json={"url": "https://example.com/price-pipeline"},
        headers=auth_tokens["headers"],
    )
    assert create.status_code == 201
    monitor_id = create.json()["id"]
    with patch(
        "warden_api.monitors.router.enqueue_job", side_effect=ConnectionError("unavailable")
    ):
        response = await client.post(
            f"/api/monitors/{monitor_id}/check", headers=auth_tokens["headers"]
        )
    assert response.status_code == 202
    outbox = await db_session.scalar(
        select(Outbox).where(Outbox.idempotency_key == f"fetch:{response.json()['job_id']}")
    )
    assert outbox is not None and outbox.status == "pending"
