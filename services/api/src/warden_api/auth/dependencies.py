"""Authentication dependencies for route protection."""

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden_api.auth.jwt import decode_access_token
from warden_api.deps import get_db
from warden_api.errors import APIError
from warden_api.security.denylist import is_denylisted
from warden_api.settings import Settings, get_settings
from warden_db.models import User


def get_redis(request: Request) -> Redis:
    """Retrieve the application Redis client from FastAPI app state."""
    redis_client = getattr(request.app.state, "redis", None)
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis connection unavailable")
    return redis_client  # type: ignore[no-any-return]


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
    session: Annotated[AsyncSession, Depends(get_db)] = None,  # type: ignore[assignment]
    redis: Annotated[Redis, Depends(get_redis)] = None,  # type: ignore[assignment]
) -> User:
    """Verify incoming Ed25519 Bearer token, check denylist, and return authenticated User."""
    if not authorization or not authorization.startswith("Bearer "):
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Missing or invalid Bearer authentication header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = authorization.removeprefix("Bearer ").strip()
    return await authenticate_user(raw_token, settings, session, redis)


async def authenticate_user(
    raw_token: str, settings: Settings, session: AsyncSession, redis: Redis
) -> User:
    """Common HTTP/WebSocket JWT, revocation, and account checks; fail closed."""

    payload = decode_access_token(raw_token, settings)

    jti = payload.get("jti")
    if not jti or await is_denylisted(redis, jti):
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Token has been revoked",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Token missing subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError as exc:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Invalid user ID format in token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Query database for user
    stmt = select(User).where(User.id == user_uuid)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="User account not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Role-gated dependency requiring admin permissions."""
    if user.role != "admin":
        raise APIError(
            status_code=403,
            title="Forbidden",
            detail="Administrator access required",
        )
    return user
