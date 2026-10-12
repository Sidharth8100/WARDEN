"""Unit and functional tests for the Monitors and Tags API.

Covering:
1. Quota enforcement (free plan limit -> 402 Payment Required)
2. Idempotency replay (same key+body replays identical response; different body -> 422)
3. Optimistic concurrency (ETag header, If-Match validation, version increment)
4. Cross-user isolation (returns 404 Not Found, never 403, preventing enumeration)
5. Tag get-or-create concurrency race (IntegrityError caught and resolved to single tag row)
6. Selector validation (invalid CSS, invalid XPath, invalid regex rejected with 422)
7. Keyset pagination stability (cursor-based non-overlapping traversal across new inserts)
"""

import asyncio
import uuid
from ipaddress import IPv4Address
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden_api.auth.passwords import hash_password
from warden_api.auth.tokens import create_new_refresh_session
from warden_api.settings import get_settings
from warden_db.models import Monitor, Tag, User


@pytest.fixture(autouse=True)
def mock_ssrf_for_crud() -> Any:
    """Mock network resolution so monitor CRUD tests pass URL safety check."""
    with (
        patch("warden_net.ssrf_check.resolve_host", new_callable=AsyncMock) as mock_res,
        patch("warden_net.ssrf_check.is_blocked_ip", return_value=None),
    ):
        mock_res.return_value = [IPv4Address("93.184.216.34")]
        yield


@pytest.mark.asyncio
async def test_quota_enforcement(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
) -> None:
    """Verify free tier users cannot exceed max_monitors (default 5).

    HTTP STATUS JUSTIFICATION:
    Returns 402 Payment Required because the limit is a commercial subscription
    boundary, prompting the client to upgrade their subscription tier.
    """
    headers = auth_tokens["headers"]

    # Free plan allows 5 monitors. Create 5 successfully.
    for i in range(5):
        res = await client.post(
            "/api/monitors",
            json={"url": f"https://example{i}.com/"},
            headers=headers,
        )
        assert res.status_code == 201, f"Failed creating monitor {i}: {res.text}"

    # 6th attempt must be rejected with 402
    res_overflow = await client.post(
        "/api/monitors",
        json={"url": "https://example-extra.com/"},
        headers=headers,
    )
    assert res_overflow.status_code == 402
    assert "limit reached" in res_overflow.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_idempotency_key_replay_and_conflict(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Verify Idempotency-Key header replays stored response and rejects body mutations."""
    headers = {**auth_tokens["headers"], "Idempotency-Key": f"idem-{uuid.uuid4().hex}"}
    unique_url = f"https://idempotent-{uuid.uuid4().hex[:8]}.com/"
    body_a = {"url": unique_url, "min_interval_s": 3600}

    # 1. First execution -> 201 Created
    res1 = await client.post("/api/monitors", json=body_a, headers=headers)
    assert res1.status_code == 201
    monitor1_id = res1.json()["id"]

    # 2. Replay with identical body -> returns 201 with identical payload verbatim
    res2 = await client.post("/api/monitors", json=body_a, headers=headers)
    assert res2.status_code == 201
    assert res2.json()["id"] == monitor1_id

    # Verify only ONE monitor was inserted in the database
    stmt = select(Monitor).where(Monitor.url == unique_url)
    res_db = await db_session.execute(stmt)
    assert len(list(res_db.scalars().all())) == 1

    # 3. Replay with DIFFERENT body -> 422 Unprocessable Entity
    body_b = {"url": f"https://different-{uuid.uuid4().hex[:8]}.com/", "min_interval_s": 7200}
    res3 = await client.post("/api/monitors", json=body_b, headers=headers)
    assert res3.status_code == 422
    assert "reused with a different request body" in res3.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_optimistic_concurrency_control(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
) -> None:
    """Verify ETag and If-Match optimistic concurrency flow."""
    headers = auth_tokens["headers"]

    # 1. Create monitor
    create_res = await client.post(
        "/api/monitors",
        json={"url": "https://concurrency-test.com/"},
        headers=headers,
    )
    assert create_res.status_code == 201
    m_id = create_res.json()["id"]
    assert create_res.json()["version"] == 1
    assert create_res.headers.get("ETag") == '"1"'

    # 2. PATCH without If-Match -> 400 Bad Request
    patch_no_etag = await client.patch(
        f"/api/monitors/{m_id}",
        json={"min_interval_s": 120},
        headers=headers,
    )
    assert patch_no_etag.status_code == 400

    # 3. PATCH with stale If-Match -> 412 Precondition Failed
    patch_stale = await client.patch(
        f"/api/monitors/{m_id}",
        json={"min_interval_s": 120},
        headers={**headers, "If-Match": '"999"'},
    )
    assert patch_stale.status_code == 412

    # 4. PATCH with matching If-Match -> 200 OK with incremented version
    patch_ok = await client.patch(
        f"/api/monitors/{m_id}",
        json={"min_interval_s": 120},
        headers={**headers, "If-Match": '"1"'},
    )
    assert patch_ok.status_code == 200
    assert patch_ok.json()["version"] == 2
    assert patch_ok.headers.get("ETag") == '"2"'

    # 5. DELETE with stale If-Match -> 412
    del_stale = await client.delete(
        f"/api/monitors/{m_id}",
        headers={**headers, "If-Match": '"1"'},
    )
    assert del_stale.status_code == 412

    # 6. DELETE with correct If-Match -> 200 OK (soft delete)
    del_ok = await client.delete(
        f"/api/monitors/{m_id}",
        headers={**headers, "If-Match": '"2"'},
    )
    assert del_ok.status_code == 200


@pytest.mark.asyncio
async def test_cross_user_isolation_returns_404(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Verify accessing another user's monitor returns 404, never 403.

    SECURITY RULE (Hard Constraint Ownership):
    Never reveal the existence of another user's resources. All queries
    filter by (id, user_id) in the same query. Non-matching IDs return 404.
    """
    # User A creates a monitor
    create_res = await client.post(
        "/api/monitors",
        json={"url": "https://user-a-target.com/"},
        headers=auth_tokens["headers"],
    )
    assert create_res.status_code == 201
    m_id = create_res.json()["id"]

    # Create User B
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

    # User B attempts GET -> 404
    get_res = await client.get(f"/api/monitors/{m_id}", headers=headers_b)
    assert get_res.status_code == 404
    assert get_res.status_code != 403

    # User B attempts PATCH -> 404
    patch_res = await client.patch(
        f"/api/monitors/{m_id}",
        json={"min_interval_s": 60},
        headers={**headers_b, "If-Match": '"1"'},
    )
    assert patch_res.status_code == 404

    # User B attempts DELETE -> 404
    del_res = await client.delete(
        f"/api/monitors/{m_id}",
        headers={**headers_b, "If-Match": '"1"'},
    )
    assert del_res.status_code == 404


@pytest.mark.asyncio
async def test_tag_get_or_create_race(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Fire concurrent requests creating the same tag; verify exactly 1 tag row created."""
    headers = auth_tokens["headers"]
    tag_name = f"race-tag-{uuid.uuid4().hex[:6]}"

    async def create_tag() -> int:
        res = await client.post(
            "/api/tags",
            json={"name": tag_name},
            headers=headers,
        )
        return res.status_code

    # Fire 5 concurrent creation requests for the exact same tag name
    statuses = await asyncio.gather(*[create_tag() for _ in range(5)])
    assert all(s in (200, 201) for s in statuses)

    # Check database: exactly one Tag row exists
    stmt = select(Tag).where(
        Tag.user_id == auth_tokens["user"].id,
        Tag.name == tag_name,
    )
    tag_res = await db_session.execute(stmt)
    tags = list(tag_res.scalars().all())
    assert len(tags) == 1


@pytest.mark.asyncio
async def test_selector_validation_css_xpath_regex(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
) -> None:
    """Verify syntactically invalid selectors return 422 with distinct error messages."""
    headers = auth_tokens["headers"]

    # 1. Invalid CSS selector
    res_css = await client.post(
        "/api/monitors",
        json={"url": "https://test-css.com/", "selector_config": {"css": "div[invalid=="}},
        headers=headers,
    )
    assert res_css.status_code == 422
    assert "css" in res_css.json().get("detail", "").lower()

    # 2. Invalid XPath selector
    res_xpath = await client.post(
        "/api/monitors",
        json={"url": "https://test-xpath.com/", "selector_config": {"xpath": "//div[invalid["}},
        headers=headers,
    )
    assert res_xpath.status_code == 422
    assert "xpath" in res_xpath.json().get("detail", "").lower()

    # Distinct messages
    assert res_css.json()["detail"] != res_xpath.json()["detail"]

    # 3. Invalid Regex
    res_regex = await client.post(
        "/api/monitors",
        json={"url": "https://test-regex.com/", "selector_config": {"regex": "(unclosed["}},
        headers=headers,
    )
    assert res_regex.status_code == 422
    assert "regex" in res_regex.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_keyset_pagination_stability_across_inserts(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
) -> None:
    """Verify keyset pagination is stable and non-overlapping even when new rows are inserted."""
    headers = auth_tokens["headers"]

    # Seed 3 monitors
    for i in range(1, 4):
        await client.post(
            "/api/monitors",
            json={"url": f"https://page-target-{i}.com/"},
            headers=headers,
        )

    # Fetch page 1 with limit=2
    page1_res = await client.get("/api/monitors?limit=2", headers=headers)
    assert page1_res.status_code == 200
    page1 = page1_res.json()
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    cursor = page1["next_cursor"]
    assert cursor is not None

    page1_ids = {m["id"] for m in page1["items"]}

    # Insert a 4th monitor AFTER fetching page 1 (classic offset pagination bug scenario)
    await client.post(
        "/api/monitors",
        json={"url": "https://page-target-new-insert.com/"},
        headers=headers,
    )

    # Fetch page 2 using the stored cursor
    page2_res = await client.get(f"/api/monitors?limit=2&cursor={cursor}", headers=headers)
    assert page2_res.status_code == 200
    page2 = page2_res.json()

    # Assert no overlap between page 1 and page 2
    page2_ids = {m["id"] for m in page2["items"]}
    assert page1_ids.isdisjoint(page2_ids)

    # Malformed cursor returns 400 Bad Request
    bad_cursor_res = await client.get("/api/monitors?cursor=not-valid-base64", headers=headers)
    assert bad_cursor_res.status_code == 400
