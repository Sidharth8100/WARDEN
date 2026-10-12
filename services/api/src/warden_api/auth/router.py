"""Authentication and session management API routes."""

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from warden_api.auth.audit import log_audit_event
from warden_api.auth.dependencies import get_current_user, get_redis
from warden_api.auth.jwt import decode_access_token
from warden_api.auth.limiter import (
    check_rate_limit,
    record_login_failure,
    reset_login_limit,
)
from warden_api.auth.passwords import (
    hash_password,
    validate_email_address,
    validate_password_strength,
    verify_dummy_password,
    verify_password,
)
from warden_api.auth.tokens import (
    clear_auth_cookies,
    create_new_refresh_session,
    hash_token,
    rotate_refresh_token,
    set_auth_cookies,
    verify_csrf_token,
)
from warden_api.deps import get_db
from warden_api.errors import APIError
from warden_api.security.denylist import add_to_denylist
from warden_api.settings import Settings, get_settings
from warden_db.models import RefreshFamily, RefreshToken, User

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    plan: str

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    """Register a new user account."""
    clean_email = validate_email_address(body.email)
    validate_password_strength(body.password)

    # Check email duplicate
    stmt = select(User).where(User.email == clean_email)
    res = await session.execute(stmt)
    if res.scalar_one_or_none():
        raise APIError(
            status_code=409,
            title="Conflict",
            detail="An account with this email address already exists",
        )

    pw_hash = hash_password(body.password)
    user = User(
        email=clean_email,
        password_hash=pw_hash,
        role="user",
        plan="free",
    )
    session.add(user)
    await session.flush()

    await log_audit_event(
        session,
        action="register_success",
        user_id=user.id,
        resource_type="user",
        resource_id=str(user.id),
        request=request,
    )
    await session.commit()
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    description=(
        "Authenticates user credentials. Returns the short-lived access token in the JSON body "
        "and sets HttpOnly 'warden_refresh' and readable 'warden_csrf' cookies. "
        "Frontend contract: Store the access token in JavaScript memory only (never localStorage) "
        "and transmit via Authorization: Bearer <token>."
    ),
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """Authenticate user credentials and issue session cookies and access token."""
    client_ip = request.client.host if request.client else None
    check_rate_limit(client_ip, body.email)

    clean_email = body.email.strip().lower()
    stmt = select(User).where(User.email == clean_email)
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()

    if user is None:
        # TIMING-ATTACK RESISTANCE: Always execute an Argon2id verification
        verify_dummy_password(body.password, settings.dummy_password_hash)
        record_login_failure(client_ip, body.email)
        await log_audit_event(
            session,
            action="login_failure",
            metadata={"email": clean_email, "reason": "user_not_found"},
            request=request,
        )
        await session.commit()
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Invalid email or password",
        )

    if not verify_password(body.password, user.password_hash):
        record_login_failure(client_ip, body.email)
        await log_audit_event(
            session,
            action="login_failure",
            user_id=user.id,
            metadata={"reason": "password_mismatch"},
            request=request,
        )
        await session.commit()
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Invalid email or password",
        )

    # Success: reset rate limit and issue tokens
    reset_login_limit(client_ip, body.email)
    raw_refresh, raw_csrf, access_token = await create_new_refresh_session(session, user, settings)

    await log_audit_event(
        session,
        action="login_success",
        user_id=user.id,
        resource_type="user",
        resource_id=str(user.id),
        request=request,
    )
    await session.commit()

    set_auth_cookies(
        response,
        refresh_token=raw_refresh,
        csrf_token=raw_csrf,
        settings=settings,
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",  # noqa: S106
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """Rotate an existing refresh token and issue a new access token."""
    verify_csrf_token(request)

    raw_refresh = request.cookies.get("warden_refresh")
    if not raw_refresh:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Missing refresh token cookie",
        )

    child_raw, child_csrf, access_token = await rotate_refresh_token(
        session, raw_refresh, settings, request
    )
    await session.commit()

    set_auth_cookies(
        response,
        refresh_token=child_raw,
        csrf_token=child_csrf,
        settings=settings,
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",  # noqa: S106
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> MessageResponse:
    """Log out the current session, revoking the refresh family and denylisting the access token."""
    verify_csrf_token(request)

    # Denylist the current access token for its remaining TTL
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.removeprefix("Bearer ").strip()
        try:
            payload = decode_access_token(raw_token, settings)
            jti = payload.get("jti")
            exp = payload.get("exp", 0)
            now_ts = int(datetime.now(UTC).timestamp())
            rem_ttl = max(1, exp - now_ts)
            if jti:
                with contextlib.suppress(NotImplementedError):
                    await add_to_denylist(redis, jti, ttl_seconds=rem_ttl)
        except Exception as exc:
            _log.debug("token_denylist_skipped_or_failed", error=str(exc))

    # Revoke current family if refresh cookie present
    raw_refresh = request.cookies.get("warden_refresh")
    if raw_refresh:
        t_hash = hash_token(raw_refresh)
        stmt = select(RefreshToken).where(RefreshToken.token_hash == t_hash)
        res = await session.execute(stmt)
        token_entry = res.scalar_one_or_none()
        if token_entry:
            now = datetime.now(UTC)
            await session.execute(
                update(RefreshFamily)
                .where(RefreshFamily.id == token_entry.family_id)
                .values(revoked_at=now, revoked_reason="logout")
            )

    clear_auth_cookies(response, settings)
    await log_audit_event(
        session,
        action="logout",
        user_id=user.id,
        request=request,
    )
    await session.commit()
    return MessageResponse(message="Logged out successfully")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    """Revoke all refresh token families for the authenticated user.

    KNOWN LIMITATION:
    Existing, non-revoked access tokens remain valid until their natural <=15-minute
    expiration time, because issued jti sets are not tracked globally per user.
    Given the short access token TTL, this is a documented security design trade-off.
    """
    verify_csrf_token(request)

    now = datetime.now(UTC)
    await session.execute(
        update(RefreshFamily)
        .where(
            RefreshFamily.user_id == user.id,
            RefreshFamily.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_reason="logout_all")
    )

    clear_auth_cookies(response, settings)
    await log_audit_event(
        session,
        action="logout_all",
        user_id=user.id,
        request=request,
    )
    await session.commit()
    return MessageResponse(message="All sessions terminated")
