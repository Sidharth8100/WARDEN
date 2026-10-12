"""Refresh token rotation, atomic concurrency claim, and CSRF protection."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from warden_api.auth.audit import log_audit_event
from warden_api.auth.jwt import create_access_token
from warden_api.errors import APIError
from warden_api.settings import Settings
from warden_db.models import RefreshFamily, RefreshToken, User


def hash_token(raw_token: str) -> bytes:
    """Hash a raw token string with SHA-256 for secure database storage."""
    return hashlib.sha256(raw_token.encode("utf-8")).digest()


def set_auth_cookies(
    response: Response,
    *,
    refresh_token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    """Set the HttpOnly refresh cookie and non-HttpOnly CSRF double-submit cookie.

    DOUBLE-SUBMIT CSRF PATTERN:
    ===========================
    1. The refresh token cookie is HttpOnly, preventing JavaScript access and XSS theft.
    2. The CSRF token cookie is readable by frontend JavaScript.
    3. On sensitive state-changing requests (/refresh, /logout), the frontend reads the
       CSRF cookie and submits its value in the 'X-CSRF-Token' header.
    4. Because a cross-site attacker cannot read cookies from other origins due to the
       Same-Origin Policy, they cannot populate the 'X-CSRF-Token' header with the
       matching random token, preventing CSRF forgery.
    """
    # 1. warden_refresh: HttpOnly, scoped to /api/auth
    response.set_cookie(
        key="warden_refresh",
        value=refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/auth",
    )
    # 2. warden_csrf: readable by JS for double-submit header
    response.set_cookie(
        key="warden_csrf",
        value=csrf_token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/auth",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Clear both authentication cookies on logout."""
    response.delete_cookie(
        key="warden_refresh",
        path="/api/auth",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        key="warden_csrf",
        path="/api/auth",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="strict",
    )


def verify_csrf_token(request: Request) -> None:
    """Verify that the X-CSRF-Token header matches the warden_csrf cookie.

    Uses secrets.compare_digest to prevent timing attacks.
    """
    cookie_csrf = request.cookies.get("warden_csrf")
    header_csrf = request.headers.get("x-csrf-token")

    if not cookie_csrf or not header_csrf:
        raise APIError(
            status_code=403,
            title="Forbidden",
            detail="Missing CSRF cookie or X-CSRF-Token header",
        )

    if not secrets.compare_digest(cookie_csrf, header_csrf):
        raise APIError(
            status_code=403,
            title="Forbidden",
            detail="CSRF token mismatch",
        )


async def create_new_refresh_session(
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> tuple[str, str, str]:
    """Create a new refresh family and initial token.

    Returns (raw_refresh_token, raw_csrf_token, access_token).
    """
    now = datetime.now(UTC)
    raw_refresh = secrets.token_urlsafe(32)
    raw_csrf = secrets.token_urlsafe(32)

    family = RefreshFamily(
        user_id=user.id,
    )
    session.add(family)
    await session.flush()

    refresh_entry = RefreshToken(
        family_id=family.id,
        token_hash=hash_token(raw_refresh),
        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
    )
    session.add(refresh_entry)
    await session.flush()

    access_token = create_access_token(user.id, user.role, settings)
    return raw_refresh, raw_csrf, access_token


async def rotate_refresh_token(
    session: AsyncSession,
    raw_refresh: str,
    settings: Settings,
    request: Request,
) -> tuple[str, str, str]:
    """Rotate an incoming refresh token atomically.

    Implements:
    1. SHA-256 token hash lookup.
    2. Family revocation and expiration checks.
    3. ATOMIC CLAIM via single SQL UPDATE ... WHERE used_at IS NULL RETURNING id.
    4. REUSE DETECTION: If claim fails on an existing token with used_at set,
       revokes the entire family and logs 'refresh_reuse_detected'.
    5. Issuance of new child token and access token in the SAME transaction.
    """
    now = datetime.now(UTC)
    t_hash = hash_token(raw_refresh)

    # Fetch token and family
    stmt = (
        select(RefreshToken, RefreshFamily, User)
        .join(RefreshFamily, RefreshToken.family_id == RefreshFamily.id)
        .join(User, RefreshFamily.user_id == User.id)
        .where(RefreshToken.token_hash == t_hash)
    )
    result = await session.execute(stmt)
    row = result.first()

    if not row:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Invalid refresh token",
        )

    token_obj, family_obj, user_obj = row

    # Check if family already revoked
    if family_obj.revoked_at is not None:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Session has been revoked",
        )

    # Check expiration
    if token_obj.expires_at <= now:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Refresh token has expired",
        )

    # --------------------------------------------------------------------------
    # ATOMIC CLAIM (Hard Constraint 5):
    # Single UPDATE statement with WHERE used_at IS NULL RETURNING id.
    # --------------------------------------------------------------------------
    update_stmt = (
        update(RefreshToken)
        .where(RefreshToken.id == token_obj.id, RefreshToken.used_at.is_(None))
        .values(used_at=now)
        .returning(RefreshToken.id)
    )
    update_res = await session.execute(update_stmt)
    claimed_id = update_res.scalar_one_or_none()

    if claimed_id is None:
        # REUSE DETECTED: A replayed, already-rotated token was submitted.
        # Invalidate the entire rotation family to protect the user from token theft.
        family_obj.revoked_at = now
        family_obj.revoked_reason = "refresh_reuse_detected"
        await session.flush()

        await log_audit_event(
            session,
            action="refresh_reuse_detected",
            user_id=family_obj.user_id,
            resource_type="refresh_family",
            resource_id=str(family_obj.id),
            metadata={"token_id": str(token_obj.id)},
            request=request,
        )
        await session.commit()

        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Refresh token reuse detected; all sessions in this chain revoked",
        )

    # Claim succeeded: issue new child token in the same family
    child_raw = secrets.token_urlsafe(32)
    child_csrf = secrets.token_urlsafe(32)

    child_token = RefreshToken(
        family_id=family_obj.id,
        parent_id=token_obj.id,
        token_hash=hash_token(child_raw),
        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
    )
    session.add(child_token)
    await session.flush()

    access_token = create_access_token(user_obj.id, user_obj.role, settings)
    return child_raw, child_csrf, access_token
