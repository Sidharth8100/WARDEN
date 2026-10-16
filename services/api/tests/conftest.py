"""Shared test fixtures for WARDEN API test suite."""

import os
import socket
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from warden_api.auth.passwords import hash_password
from warden_api.auth.tokens import create_new_refresh_session
from warden_api.main import create_app
from warden_api.settings import get_settings
from warden_db.models import User

DEFAULT_DEV_DB_URL = "postgresql+asyncpg://warden:Flame%402005Kaiser@127.0.0.1:5433/warden"


def _is_postgres_reachable(host: str = "127.0.0.1", port: int = 5433) -> bool:
    """Check whether Postgres is accepting and responding to queries on localhost:5433."""
    try:
        with socket.create_connection((host, port), timeout=0.3) as s:
            s.settimeout(0.3)
            # Send standard PostgreSQL SSLRequest probe (code 80877103)
            # Responsive Postgres servers immediately return b'N' (or b'S').
            s.sendall(b"\x00\x00\x00\x08\x04\xd2\x16/")
            resp = s.recv(1)
            return len(resp) > 0
    except (OSError, TimeoutError):
        return False


def _is_redis_reachable(host: str = "127.0.0.1", port: int = 6379) -> bool:
    """Check whether Redis is accepting connections on localhost:6379."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure required environment variables are set before each test."""
    db_url = os.environ.get("WARDEN_DATABASE_URL")
    if not db_url:
        db_url = (
            DEFAULT_DEV_DB_URL
            if _is_postgres_reachable()
            else "postgresql+asyncpg://test:test@localhost:5432/test"
        )
    monkeypatch.setenv("WARDEN_DATABASE_URL", db_url)
    monkeypatch.setenv("WARDEN_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("WARDEN_ENV", "dev")
    monkeypatch.setenv("WARDEN_JWT_PRIVATE_KEY_PATH", "keys/jwt_ed25519_private.pem")
    monkeypatch.setenv("WARDEN_JWT_PUBLIC_KEY_PATH", "keys/jwt_ed25519_public.pem")
    monkeypatch.setenv("WARDEN_JWT_KID", "warden-test-key")


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """Clear settings lru_cache so environment patches take effect."""
    get_settings.cache_clear()


@pytest.fixture
def fast_hasher(monkeypatch: pytest.MonkeyPatch) -> PasswordHasher:
    """Low-cost Argon2id hasher for test speed ONLY.

    SECURITY RULE (Hard Constraint 2):
    Argon2id in production uses library recommended defaults (time_cost=3, memory_cost=65536,
    parallelism=4). Hand-tuning production parameters down is strictly forbidden.
    Tests that perform many hash operations use this fixture to patch the internal
    hasher with low-cost parameters (time_cost=1, memory_cost=1024, parallelism=1).
    This fixture is strictly confined to test suites and never imported by application code.
    """
    hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    monkeypatch.setattr("warden_api.auth.passwords._hasher", hasher)
    return hasher


@pytest_asyncio.fixture
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Provide an async database engine when live postgres is reachable."""
    if not _is_postgres_reachable():
        pytest.skip("Postgres on localhost:5433 is unreachable")
    url = os.environ.get("WARDEN_DATABASE_URL", DEFAULT_DEV_DB_URL)
    engine = create_async_engine(url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session rolled back after test completion."""
    sessionmaker = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session


@pytest_asyncio.fixture
async def live_redis() -> AsyncGenerator[Redis, None]:  # type: ignore[type-arg]
    """Provide a live Redis client if available."""
    if not _is_redis_reachable():
        pytest.skip("Redis on localhost:6379 is unreachable")
    client = Redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def client(fast_hasher: PasswordHasher) -> AsyncGenerator[AsyncClient, None]:
    """Async test client connected to the application.

    Uses live DB/Redis if available, otherwise mocks app.state for unit isolation.
    """
    app = create_app()

    if _is_postgres_reachable() and _is_redis_reachable():
        url = os.environ.get("WARDEN_DATABASE_URL", DEFAULT_DEV_DB_URL)
        engine = create_async_engine(url, pool_pre_ping=True)
        sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
        redis_client = Redis.from_url("redis://127.0.0.1:6379/0", decode_responses=False)
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.redis = redis_client
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http.get = AsyncMock(return_value=mock_response)
        app.state.http_client = mock_http

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac

        await redis_client.aclose()
        await engine.dispose()
    else:
        # Mock mode
        mock_session = AsyncMock()
        mock_sessionmaker = MagicMock()
        mock_sessionmaker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessionmaker.return_value.__aexit__ = AsyncMock(return_value=False)
        app.state.sessionmaker = mock_sessionmaker
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        app.state.redis = mock_redis
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http.get = AsyncMock(return_value=mock_response)
        app.state.http_client = mock_http

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession, fast_hasher: PasswordHasher) -> User:
    """Create and return an isolated test user in the database."""
    import uuid

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=email,
        password_hash=hash_password("ValidPassword123!"),
        role="user",
        plan="free",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_tokens(
    db_session: AsyncSession,
    test_user: User,
) -> dict[str, Any]:
    """Issue valid access token, refresh token, and CSRF token for test_user."""
    settings = get_settings()
    raw_refresh, raw_csrf, access_token = await create_new_refresh_session(
        db_session, test_user, settings
    )
    await db_session.commit()
    return {
        "user": test_user,
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "csrf_token": raw_csrf,
        "headers": {
            "Authorization": f"Bearer {access_token}",
            "X-CSRF-Token": raw_csrf,
        },
        "cookies": {
            "warden_refresh": raw_refresh,
            "warden_csrf": raw_csrf,
        },
    }
