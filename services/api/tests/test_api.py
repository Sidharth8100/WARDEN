"""Unit tests for the WARDEN API service.

WHAT WE TEST WITHOUT A REAL DATABASE OR REDIS:
===============================================
These tests verify API behaviour using FastAPI's TestClient, which makes
HTTP requests to the app WITHOUT starting a real HTTP server. The app
processes the requests in-process (no network involved).

For tests that would normally need a real database or Redis, we use
monkeypatching: we replace the real function that talks to the database
with a fake one that immediately returns a success or failure response.
This is called "mocking" and it lets us test our response-building logic
independently of whether Postgres is running.

KEY TESTING TOOLS:
==================
- pytest.fixture: Creates reusable setup/teardown for tests
- httpx.AsyncClient + ASGITransport: Makes async HTTP requests to the app
  without a real server (ASGI = Asynchronous Server Gateway Interface)
- monkeypatch: pytest's built-in way to temporarily replace functions/values
- unittest.mock.AsyncMock: A mock that behaves like an async function (coroutine)
"""

import os
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures: reusable setup shared across multiple tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required environment variables before each test.

    Without this, importing settings.py would fail because WARDEN_DATABASE_URL
    and WARDEN_REDIS_URL are required fields with no defaults.

    autouse=True means this fixture applies to EVERY test in this file
    without needing to declare it explicitly.
    """
    monkeypatch.setenv("WARDEN_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("WARDEN_REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """Clear the lru_cache on get_settings() before each test.

    Since get_settings() is cached with @lru_cache, each test would see
    the same Settings instance unless we clear the cache. This fixture
    ensures each test starts fresh (especially important when different
    tests set different env vars).
    """
    from warden_api.settings import get_settings

    get_settings.cache_clear()


def make_test_app() -> Any:
    """Create a fresh app with a fake app.state for testing.

    We don't use the real lifespan (which would try to connect to Postgres/Redis).
    Instead, we create the app and manually set up app.state with mock objects.
    """
    from warden_api.main import create_app

    app = create_app()

    # Replace the real lifespan resources with mocks.
    # Tests that need specific behaviour override these individually.

    # Mock sessionmaker: calling it returns an async context manager
    # that yields a mock session.
    mock_session = AsyncMock()
    mock_sessionmaker = MagicMock()
    mock_sessionmaker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_sessionmaker.return_value.__aexit__ = AsyncMock(return_value=False)
    app.state.sessionmaker = mock_sessionmaker

    # Mock Redis client: ping() returns True by default
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    app.state.redis = mock_redis

    # Mock HTTP client: get() returns a response with status 200 by default
    mock_http = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_http.get = AsyncMock(return_value=mock_response)
    app.state.http_client = mock_http

    return app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client for the app.

    ASGITransport connects httpx directly to our FastAPI app in-process.
    No network, no port binding - just function calls.
    base_url is required by httpx but doesn't affect routing.

    Return type is AsyncGenerator because this is an async generator function
    (it uses `yield`). pytest-asyncio handles calling it as a fixture.
    """
    app = make_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Settings tests
# ---------------------------------------------------------------------------


class TestSettings:
    """Verify Settings class behaviour."""

    def test_settings_repr_does_not_leak_secrets(self) -> None:
        """SecretStr fields must show '**********' in repr, not the real value.

        This is critical for log safety: if settings are accidentally logged,
        the real database URL/password should not appear in log files.
        """
        from warden_api.settings import get_settings

        settings = get_settings()
        settings_repr = repr(settings)

        # The actual URL value should NOT appear in repr
        assert "postgresql+asyncpg" not in settings_repr, (
            "database_url leaked in Settings repr! SecretStr is not working."
        )
        assert "redis://" not in settings_repr, (
            "redis_url leaked in Settings repr! SecretStr is not working."
        )
        # pydantic SecretStr shows '**********' for secret fields
        assert "**" in settings_repr

    def test_get_secret_value_returns_real_url(self) -> None:
        """get_secret_value() should return the actual URL string."""
        from warden_api.settings import get_settings

        settings = get_settings()
        db_url = settings.database_url.get_secret_value()
        assert db_url.startswith("postgresql+asyncpg://")

    def test_default_objectstore_url(self) -> None:
        """objectstore_url should default to the in-compose hostname."""
        from warden_api.settings import get_settings

        settings = get_settings()
        assert settings.objectstore_url == "http://objectstore:8333"


# ---------------------------------------------------------------------------
# /live endpoint tests
# ---------------------------------------------------------------------------


class TestLive:
    """Tests for GET /live (liveness probe)."""

    async def test_live_returns_200(self, client: AsyncClient) -> None:
        """GET /live must always return HTTP 200."""
        response = await client.get("/live")
        assert response.status_code == 200

    async def test_live_returns_x_request_id_header(self, client: AsyncClient) -> None:
        """Every response must include an x-request-id header.

        This is set by RequestIDMiddleware. Testing it here confirms the
        middleware is correctly wired into the app.
        """
        response = await client.get("/live")
        assert "x-request-id" in response.headers
        # The request ID should be a non-empty string
        assert len(response.headers["x-request-id"]) > 0

    async def test_live_echoes_safe_client_request_id(self, client: AsyncClient) -> None:
        """A valid x-request-id from the client should be echoed back."""
        custom_id = "my-request-abc-123"
        response = await client.get("/live", headers={"x-request-id": custom_id})
        assert response.headers["x-request-id"] == custom_id

    async def test_live_replaces_unsafe_request_id(self, client: AsyncClient) -> None:
        """An x-request-id containing unsafe characters should be replaced.

        The middleware regex only allows [A-Za-z0-9-]{1,64}.
        A header with spaces, semicolons, or other characters should be
        rejected and a new UUID-based ID should be generated instead.
        """
        unsafe_id = "bad id; DROP TABLE users--"
        response = await client.get("/live", headers={"x-request-id": unsafe_id})
        # The response ID must be different from the unsafe one
        response_id = response.headers["x-request-id"]
        assert response_id != unsafe_id
        # And it should be a valid hex UUID (32 chars, all hex digits)
        assert len(response_id) == 32
        assert all(c in "0123456789abcdef" for c in response_id)

    async def test_live_replaces_too_long_request_id(self, client: AsyncClient) -> None:
        """An x-request-id longer than 64 chars should be replaced."""
        too_long = "a" * 65
        response = await client.get("/live", headers={"x-request-id": too_long})
        response_id = response.headers["x-request-id"]
        assert response_id != too_long


# ---------------------------------------------------------------------------
# /health endpoint tests (monkeypatched - no real services)
# ---------------------------------------------------------------------------


class TestHealth:
    """Tests for GET /health (dependency health check)."""

    async def test_health_returns_ok_when_all_pass(self, client: AsyncClient) -> None:
        """When all three checks succeed, /health should return 200 and status=ok."""
        response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "postgres" in body["checks"]
        assert "redis" in body["checks"]
        assert "objectstore" in body["checks"]

    async def test_health_returns_503_when_postgres_fails(self) -> None:
        """When Postgres is unreachable, /health should return 503 with status=degraded."""
        from sqlalchemy.exc import OperationalError

        app = make_test_app()

        # Make the mock session raise an exception to simulate DB failure
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=OperationalError("could not connect", None, Exception("conn failed"))
        )
        mock_sessionmaker = MagicMock()
        mock_sessionmaker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessionmaker.return_value.__aexit__ = AsyncMock(return_value=False)
        app.state.sessionmaker = mock_sessionmaker

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["checks"]["postgres"]["ok"] is False
        # Error message must not contain the URL
        assert "postgresql+asyncpg" not in body["checks"]["postgres"].get("error", "")

    async def test_health_returns_503_when_redis_fails(self) -> None:
        """When Redis is unreachable, /health should return 503 with status=degraded."""

        app = make_test_app()
        app.state.redis.ping = AsyncMock(side_effect=ConnectionError("Redis connection refused"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["checks"]["redis"]["ok"] is False
        # Error message must not contain the Redis URL
        assert "redis://" not in body["checks"]["redis"].get("error", "")

    async def test_health_returns_503_when_objectstore_fails(self) -> None:
        """When the objectstore is unreachable, /health should return 503."""
        import httpx

        app = make_test_app()
        app.state.http_client.get = AsyncMock(
            side_effect=httpx.ConnectError("objectstore unreachable")
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["checks"]["objectstore"]["ok"] is False


# ---------------------------------------------------------------------------
# Lifespan tests
# ---------------------------------------------------------------------------


class TestLifespan:
    """Verify app lifespan starts and stops cleanly."""

    async def test_lifespan_starts_and_stops_with_unreachable_services(self) -> None:
        """The app must boot successfully even if Redis and the objectstore are down.

        This tests the 'lazy connection' design: creating the redis client object
        does NOT open a connection. The connection only happens when the first
        command is sent (e.g. PING in the health check).
        """
        # Set env vars needed for lifespan (real DB URL format but unreachable host)
        os.environ["WARDEN_DATABASE_URL"] = (
            "postgresql+asyncpg://test:test@unreachable_host:5432/test"
        )
        os.environ["WARDEN_REDIS_URL"] = "redis://unreachable_host:6379/0"

        from warden_api.main import create_app
        from warden_api.settings import get_settings

        get_settings.cache_clear()

        app = create_app()

        # lifespan_context() runs the lifespan as a context manager.
        # If startup fails, this raises an exception and the test fails.
        async with app.router.lifespan_context(app):
            # App is "running" - check that state was populated
            assert hasattr(app.state, "engine")
            assert hasattr(app.state, "redis")
            assert hasattr(app.state, "http_client")

        # After the `async with` block exits, shutdown has run.
        # The test passes if no exception was raised during startup or shutdown.
