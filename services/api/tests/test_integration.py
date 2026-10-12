"""End-to-end integration test against live Docker services.

Verifies the full user journey:
Register -> Login -> Create Monitor -> List -> Update ETag -> Soft-delete.
"""

import uuid
from ipaddress import IPv4Address
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden_db.models import Monitor


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_user_lifecycle_integration(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """End-to-end test of register, login, monitor CRUD, optimistic locking, and soft delete."""
    with (
        patch("warden_net.ssrf_check.resolve_host", new_callable=AsyncMock) as mock_res,
        patch("warden_net.ssrf_check.is_blocked_ip", return_value=None),
    ):
        mock_res.return_value = [IPv4Address("93.184.216.34")]

        email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
        password = "E2EPassword123!@#"  # noqa: S105

        # 1. Register
        reg_res = await client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        assert reg_res.status_code == 201
        user_id = reg_res.json()["id"]
        assert user_id is not None

        # 2. Login
        login_res = await client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert login_res.status_code == 200
        token_data = login_res.json()
        access_token = token_data["access_token"]
        auth_headers = {"Authorization": f"Bearer {access_token}"}

        # 3. Create Monitor
        create_res = await client.post(
            "/api/monitors",
            json={
                "url": "https://integration-target.com/",
                "min_interval_s": 3600,
                "max_interval_s": 7200,
                "tags": ["prod", "web"],
            },
            headers=auth_headers,
        )
        assert create_res.status_code == 201, f"Failed: {create_res.json()}"
        monitor_data = create_res.json()
        monitor_id = monitor_data["id"]
        assert monitor_data["version"] == 1
        etag = create_res.headers.get("ETag")
        assert etag == '"1"'

        # 4. List Monitors - confirms monitor appears in listing
        list_res = await client.get("/api/monitors", headers=auth_headers)
        assert list_res.status_code == 200
        items = list_res.json()["items"]
        assert any(m["id"] == monitor_id for m in items)

        # 5. Update Monitor with correct If-Match ETag
        update_res = await client.patch(
            f"/api/monitors/{monitor_id}",
            json={"min_interval_s": 7200},
            headers={**auth_headers, "If-Match": etag},
        )
        assert update_res.status_code == 200
        updated_data = update_res.json()
        assert updated_data["min_interval_s"] == 7200
        assert updated_data["version"] == 2
        new_etag = update_res.headers.get("ETag")
        assert new_etag == '"2"'

        # 6. Soft-delete Monitor with new ETag
        delete_res = await client.delete(
            f"/api/monitors/{monitor_id}",
            headers={**auth_headers, "If-Match": new_etag},
        )
        assert delete_res.status_code == 200

        # 7. List Monitors - confirms soft-deleted monitor is excluded from listing
        list_after_del = await client.get("/api/monitors", headers=auth_headers)
        assert list_after_del.status_code == 200
        remaining_items = list_after_del.json()["items"]
        assert not any(m["id"] == monitor_id for m in remaining_items)

        # 8. Direct database check: verify row still exists with state='deleted'
        db_stmt = select(Monitor).where(Monitor.id == uuid.UUID(monitor_id))
        db_row = (await db_session.execute(db_stmt)).scalar_one_or_none()
        assert db_row is not None
        assert db_row.state == "deleted"
        assert db_row.version == 3
