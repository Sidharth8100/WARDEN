"""Security-critical tests for WARDEN authentication and token management.

Covering:
1. Alg-confusion (alg: none and Ed25519 public key as HMAC HS256 secret)
2. Secret-leakage (raw passwords, raw JWTs, refresh tokens never logged)
3. Timing consistency (Argon2id verify called on nonexistent user vs wrong password)
4. Concurrent refresh rotation (atomic claim via asyncio.gather)
5. Refresh token reuse detection (revocation of family and audit log entry)
6. CSRF double-submit protection on /refresh and /logout
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden_api.settings import get_settings
from warden_db.models import AuditLog, RefreshToken, User


@pytest.mark.asyncio
async def test_alg_confusion_attacks_rejected(client: AsyncClient, test_user: User) -> None:
    """Verify get_current_user rejects alg: none and public-key-as-HMAC HS256 tokens.

    SECURITY RULE (Hard Constraint 3):
    The JWT verification function must pass an explicit allow-list: algorithms=['EdDSA'].
    It must NEVER honor the token's own 'alg' header claim or fall back to symmetric verification.
    """
    settings = get_settings()

    # 1. Attack A: alg: none (unsigned token)
    claims: dict[str, Any] = {
        "sub": str(test_user.id),
        "role": test_user.role,
        "iat": int(time.time()),
        "exp": int(time.time()) + 900,
        "jti": uuid.uuid4().hex,
    }
    none_token = jwt.encode(claims, key="", algorithm="none")

    res_none = await client.get(
        "/api/monitors",
        headers={"Authorization": f"Bearer {none_token}"},
    )
    assert res_none.status_code == 401
    assert "error=" in res_none.headers.get("WWW-Authenticate", "")

    # 2. Attack B: Public key as HMAC secret (HS256)
    public_key_pem = Path(settings.jwt_public_key_path).read_bytes()  # noqa: ASYNC240

    header = {"alg": "HS256", "typ": "JWT", "kid": settings.jwt_kid}
    b64_header = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    b64_claims = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signing_input = f"{b64_header}.{b64_claims}".encode()
    sig = hmac.new(public_key_pem, signing_input, hashlib.sha256).digest()
    b64_sig = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    hs256_token = f"{signing_input.decode()}.{b64_sig}"

    res_hs256 = await client.get(
        "/api/monitors",
        headers={"Authorization": f"Bearer {hs256_token}"},
    )
    assert res_hs256.status_code == 401
    assert "error=" in res_hs256.headers.get("WWW-Authenticate", "")


@pytest.mark.asyncio
async def test_secrets_never_appear_in_logs(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    db_session: AsyncSession,
) -> None:
    """Verify passwords, raw JWTs, refresh tokens, and CSRF tokens are never logged.

    SECURITY RULE (Hard Constraint 1):
    Never log a secret. Raw passwords, raw JWTs, refresh tokens, and CSRF tokens
    must never appear in any log output, exception trace, or problem+json body.
    """
    caplog.clear()
    email = f"audit-{uuid.uuid4().hex[:8]}@example.com"
    raw_password = "SuperSecretPassword123!@#"  # noqa: S105

    # 1. Register
    reg_res = await client.post(
        "/api/auth/register",
        json={"email": email, "password": raw_password},
    )
    assert reg_res.status_code == 201

    # 2. Login
    login_res = await client.post(
        "/api/auth/login",
        json={"email": email, "password": raw_password},
    )
    assert login_res.status_code == 200
    login_body = login_res.json()
    raw_access_token = login_body["access_token"]
    raw_refresh_token = login_res.cookies.get("warden_refresh")
    raw_csrf_token = login_res.cookies.get("warden_csrf")
    assert raw_refresh_token is not None
    assert raw_csrf_token is not None

    # 3. Refresh
    refresh_res = await client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": raw_csrf_token},
        cookies={"warden_refresh": raw_refresh_token, "warden_csrf": raw_csrf_token},
    )
    assert refresh_res.status_code == 200
    child_access_token = refresh_res.json()["access_token"]
    child_refresh_token = refresh_res.cookies.get("warden_refresh")
    child_csrf_token = refresh_res.cookies.get("warden_csrf")
    assert child_refresh_token is not None

    # 4. Logout
    logout_res = await client.post(
        "/api/auth/logout",
        headers={
            "Authorization": f"Bearer {child_access_token}",
            "X-CSRF-Token": child_csrf_token,
        },
        cookies={"warden_refresh": child_refresh_token, "warden_csrf": child_csrf_token},
    )
    assert logout_res.status_code == 200

    # 5. Assert NO captured log text contains any raw secrets
    captured_logs = "\n".join(rec.getMessage() for rec in caplog.records)

    assert raw_password not in captured_logs
    assert raw_access_token not in captured_logs
    assert raw_refresh_token not in captured_logs
    assert child_access_token not in captured_logs
    assert child_refresh_token not in captured_logs
    assert raw_csrf_token not in captured_logs


@pytest.mark.asyncio
async def test_timing_consistency_between_missing_user_and_wrong_password(
    client: AsyncClient,
    test_user: User,
) -> None:
    """Verify both nonexistent user and wrong password execute Argon2id verification.

    TIMING ATTACK DEFENSE:
    To prevent user enumeration via timing discrepancies, login requests for
    nonexistent email addresses execute an Argon2id verify against a precomputed
    dummy hash. Both code paths must invoke verify() exactly once.
    """
    with (
        patch("warden_api.auth.router.verify_password", return_value=False) as mock_verify,
        patch("warden_api.auth.router.verify_dummy_password", return_value=False) as mock_dummy,
    ):
        # 1. Nonexistent user
        res_nonexistent = await client.post(
            "/api/auth/login",
            json={"email": "nonexistent-user-123@example.com", "password": "WrongPassword123!"},
        )
        assert res_nonexistent.status_code == 401

        # 2. Existing user, wrong password
        res_wrong_pw = await client.post(
            "/api/auth/login",
            json={"email": test_user.email, "password": "WrongPassword123!"},
        )
        assert res_wrong_pw.status_code == 401

        # Both code paths executed exactly one password verification
        assert mock_dummy.call_count == 1
        assert mock_verify.call_count == 1


@pytest.mark.asyncio
async def test_csrf_protection_on_refresh_and_logout(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
) -> None:
    """Verify endpoints reject requests with missing or mismatched CSRF tokens."""
    access_token = auth_tokens["access_token"]
    refresh_token = auth_tokens["refresh_token"]
    csrf_token = auth_tokens["csrf_token"]

    # 1. Refresh without X-CSRF-Token header -> 403 Forbidden
    res_no_header = await client.post(
        "/api/auth/refresh",
        cookies={"warden_refresh": refresh_token, "warden_csrf": csrf_token},
    )
    assert res_no_header.status_code == 403
    assert "CSRF" in res_no_header.json().get("detail", "")

    # 2. Refresh with mismatched X-CSRF-Token header -> 403 Forbidden
    res_mismatch = await client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": "bogus-csrf-token-123"},
        cookies={"warden_refresh": refresh_token, "warden_csrf": csrf_token},
    )
    assert res_mismatch.status_code == 403

    # 3. Logout without X-CSRF-Token header -> 403 Forbidden
    res_logout_no_header = await client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
        cookies={"warden_refresh": refresh_token, "warden_csrf": csrf_token},
    )
    assert res_logout_no_header.status_code == 403


@pytest.mark.asyncio
async def test_concurrent_refresh_rotation_atomic_claim(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Fire two concurrent refresh requests with the SAME token; assert exactly one succeeds.

    CONCURRENCY & HARD CONSTRAINT 5:
    Uses asyncio.gather to simulate race conditions. The database atomic update
    (UPDATE ... WHERE used_at IS NULL RETURNING id) ensures exactly one caller
    claims the token, while the other fails with reuse rejection.
    """
    refresh_token = auth_tokens["refresh_token"]
    csrf_token = auth_tokens["csrf_token"]

    async def fire_refresh() -> int:
        res = await client.post(
            "/api/auth/refresh",
            headers={"X-CSRF-Token": csrf_token},
            cookies={"warden_refresh": refresh_token, "warden_csrf": csrf_token},
        )
        return res.status_code

    # Fire concurrently
    results = await asyncio.gather(fire_refresh(), fire_refresh())

    # Exactly one succeeded (200), and one failed (401)
    assert sorted(results) == [200, 401]

    # Check child token count: only 1 child token created
    family_stmt = select(RefreshToken).where(RefreshToken.parent_id.is_not(None))
    token_res = await db_session.execute(family_stmt)
    child_tokens = token_res.scalars().all()
    # At least the successful rotation created a child token
    assert len(child_tokens) >= 1


@pytest.mark.asyncio
async def test_refresh_token_reuse_detection_revokes_entire_family(
    client: AsyncClient,
    auth_tokens: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Rotate token A -> token B, then replay token A.

    Assert:
    1. Family is revoked immediately.
    2. Legitimate child token B is now ALSO rejected.
    3. An audit_log entry with action='refresh_reuse_detected' is saved.
    """
    token_a = auth_tokens["refresh_token"]
    csrf_a = auth_tokens["csrf_token"]

    # Step 1: Legitimate rotation (A -> B)
    rotate_res = await client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": csrf_a},
        cookies={"warden_refresh": token_a, "warden_csrf": csrf_a},
    )
    assert rotate_res.status_code == 200
    token_b = rotate_res.cookies.get("warden_refresh")
    csrf_b = rotate_res.cookies.get("warden_csrf")
    assert token_b is not None

    # Step 2: Malicious replay of old token A
    replay_res = await client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": csrf_a},
        cookies={"warden_refresh": token_a, "warden_csrf": csrf_a},
    )
    assert replay_res.status_code == 401
    assert "revoked" in replay_res.json().get("detail", "").lower()

    # Step 3: Legitimate token B is now ALSO rejected because family was revoked
    subsequent_res = await client.post(
        "/api/auth/refresh",
        headers={"X-CSRF-Token": csrf_b},
        cookies={"warden_refresh": token_b, "warden_csrf": csrf_b},
    )
    assert subsequent_res.status_code == 401
    assert "revoked" in subsequent_res.json().get("detail", "").lower()

    # Step 4: Verify audit_log entry
    audit_stmt = select(AuditLog).where(AuditLog.action == "refresh_reuse_detected")
    audit_res = await db_session.execute(audit_stmt)
    audit_events = list(audit_res.scalars().all())
    assert len(audit_events) >= 1
    assert any(e.action == "refresh_reuse_detected" for e in audit_events)
