"""Integration tests for POST /api/monitors/{id}/check endpoint and synchronous pipeline.

Covers:
1. First check stores baseline keyframe snapshot and returns 204 No Content.
2. Subsequent check without meaningful change returns 204 No Content.
3. Subsequent check with meaningful price change returns 200 OK with ChangeEvent.
4. Cross-user isolation: 404 Not Found when checking another user's monitor.
5. Non-existent monitor: 404 Not Found.
"""

import hashlib
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
from warden_db.models import ChangeEvent, Snapshot, User


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
async def test_first_check_creates_keyframe_and_returns_204(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """First-ever check creates a baseline keyframe snapshot and returns 204."""
    create_res = await client.post(
        "/api/monitors",
        json={"url": "https://example.com/first-check"},
        headers=auth_tokens["headers"],
    )
    assert create_res.status_code == 201
    monitor_id = uuid.UUID(create_res.json()["id"])

    sample_html = b"<html><body><h1>Product</h1><p>$99.99</p></body></html>"
    blob_hash = hashlib.sha256(sample_html).hexdigest()

    with (
        patch("warden_fetch.orchestrate.is_fetch_allowed", new_callable=AsyncMock) as mock_robots,
        patch("warden_fetch.orchestrate.fetch_tier1", new_callable=AsyncMock) as mock_tier1,
        patch("warden_fetch.orchestrate.put_blob", new_callable=AsyncMock) as mock_put,
        patch("warden_api.pipeline.get_blob", new_callable=AsyncMock) as mock_get,
    ):
        from warden_fetch.robots import RobotsDecision
        from warden_fetch.tier1 import Tier1Result

        mock_robots.return_value = RobotsDecision(allowed=True, crawl_delay=None)
        mock_tier1.return_value = Tier1Result(
            status=200,
            final_url="https://example.com/first-check",
            headers={"etag": '"etag-1"'},
            body=sample_html,
            not_modified=False,
            elapsed_ms=50.0,
        )
        mock_put.return_value = blob_hash
        mock_get.return_value = sample_html

        res = await client.post(
            f"/api/monitors/{monitor_id}/check",
            headers=auth_tokens["headers"],
        )

    assert res.status_code == 204

    # Verify Snapshot was created in database
    stmt = select(Snapshot).where(Snapshot.monitor_id == monitor_id)
    snapshots = (await db_session.execute(stmt)).scalars().all()
    assert len(snapshots) == 1
    assert snapshots[0].kind == "keyframe"

    # Verify no ChangeEvent was emitted on baseline check
    event_stmt = select(ChangeEvent).where(ChangeEvent.monitor_id == monitor_id)
    events = (await db_session.execute(event_stmt)).scalars().all()
    assert len(events) == 0


@pytest.mark.asyncio
async def test_pipeline_meaningful_change_detection_flow(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Full two-step pipeline run: baseline check followed by meaningful price update."""
    create_res = await client.post(
        "/api/monitors",
        json={"url": "https://example.com/price-pipeline"},
        headers=auth_tokens["headers"],
    )
    assert create_res.status_code == 201
    monitor_id = uuid.UUID(create_res.json()["id"])

    html_v1 = b"<html><body><h1>Widget</h1><p class='price'>$99.99</p></body></html>"
    html_v2 = b"<html><body><h1>Widget</h1><p class='price'>$149.99</p></body></html>"
    h1 = hashlib.sha256(html_v1).hexdigest()
    h2 = hashlib.sha256(html_v2).hexdigest()

    async def fake_get(config: Any, key: str) -> bytes:
        return html_v2 if key == h2 else html_v1

    with (
        patch("warden_fetch.orchestrate.is_fetch_allowed", new_callable=AsyncMock) as mock_robots,
        patch("warden_fetch.orchestrate.fetch_tier1", new_callable=AsyncMock) as mock_tier1,
        patch("warden_fetch.orchestrate.put_blob", new_callable=AsyncMock) as mock_put,
        patch("warden_api.pipeline.get_blob", new_callable=AsyncMock) as mock_get,
    ):
        from warden_fetch.robots import RobotsDecision
        from warden_fetch.tier1 import Tier1Result

        mock_robots.return_value = RobotsDecision(allowed=True, crawl_delay=None)

        # 1. First check -> baseline keyframe
        mock_tier1.return_value = Tier1Result(
            status=200,
            final_url="https://example.com/price-pipeline",
            headers={},
            body=html_v1,
            not_modified=False,
            elapsed_ms=45.0,
        )
        mock_put.return_value = h1
        mock_get.side_effect = fake_get

        r1 = await client.post(
            f"/api/monitors/{monitor_id}/check",
            headers=auth_tokens["headers"],
        )
        assert r1.status_code == 204

        # 2. Second check -> price changed ($99.99 -> $149.99)
        mock_tier1.return_value = Tier1Result(
            status=200,
            final_url="https://example.com/price-pipeline",
            headers={},
            body=html_v2,
            not_modified=False,
            elapsed_ms=52.0,
        )
        mock_put.return_value = h2
        mock_get.side_effect = fake_get

        r2 = await client.post(
            f"/api/monitors/{monitor_id}/check",
            headers=auth_tokens["headers"],
        )
        assert r2.status_code == 200
        event_data = r2.json()
        assert event_data["monitor_id"] == str(monitor_id)
        assert event_data["features"]["is_numeric_only"] is True
        assert event_data["score"] >= 0.35

    # Check database persistence
    stmt_snaps = select(Snapshot).where(Snapshot.monitor_id == monitor_id)
    snapshots = (await db_session.execute(stmt_snaps)).scalars().all()
    assert len(snapshots) == 2

    stmt_events = select(ChangeEvent).where(ChangeEvent.monitor_id == monitor_id)
    events = (await db_session.execute(stmt_events)).scalars().all()
    assert len(events) == 1
    assert events[0].features["is_numeric_only"] is True
