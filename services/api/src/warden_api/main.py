"""WARDEN API application factory.

WHAT IS AN APPLICATION FACTORY?
================================
Instead of creating the FastAPI app at module level (like `app = FastAPI()`),
we use a FACTORY function: `create_app()`. This means:

  1. TESTABILITY: Tests can call `create_app(settings=test_settings)` to get
     a fresh app with test configuration, without affecting other tests.
  2. MULTIPLE INSTANCES: If needed, you can create two apps in the same process
     with different settings (useful in some testing scenarios).
  3. NO IMPORT SIDE EFFECTS: Importing this module doesn't start database
     connections or read environment variables - only calling create_app() does.

WHAT IS A LIFESPAN?
===================
FastAPI's `lifespan` is a context manager that runs ONCE per application:
  - Code BEFORE `yield` runs at STARTUP (when the server starts)
  - Code AFTER `yield` runs at SHUTDOWN (when the server stops or Ctrl+C)

This is where we create "expensive" resources that should be shared across all
requests: database connection pool, Redis client, HTTP client. We store them
on `app.state` so every request can access them via `request.app.state`.

WHY NOT CREATE THESE IN EVERY REQUEST?
=======================================
- Database pool: Creating a new pool per request would take ~100ms and
  you'd quickly run out of Postgres connections under load.
- Redis client: Same reason - one shared client handles connection pooling.
- httpx.AsyncClient: Reuses TCP connections (HTTP keep-alive), which is
  ~10x faster than opening a new TCP connection for every health check.

DEPENDENCY GRAPH DURING A REQUEST:
====================================
  HTTP Request arrives
      → RequestIDMiddleware.dispatch() (assigns request ID, logs)
          → Route handler (e.g. /health)
              → deps.get_session() borrows a session from app.state.sessionmaker
              → reads from app.state.redis directly
              → reads from app.state.http_client directly
          ← Response built
      ← Access log written, x-request-id header added
  HTTP Response sent
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI

from warden_api.health import router as health_router
from warden_api.logconfig import configure_logging
from warden_api.middleware import RequestIDMiddleware
from warden_api.settings import Settings, get_settings

_log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: create shared resources on startup, close on shutdown.

    FastAPI calls this once when the server starts (code before yield)
    and once when the server stops (code after yield).

    All resources are stored on app.state so that:
      - Route handlers access them via request.app.state
      - Tests can inject fake resources by setting app.state directly
    """
    settings: Settings = app.state.settings

    # ------------------------------------------------------------------
    # STARTUP
    # ------------------------------------------------------------------
    _log.info("startup", log_level=settings.log_level, json_logs=settings.json_logs)

    # --- Database engine and session factory ----------------------------
    # We import here (not at module level) to keep startup explicit and
    # to avoid circular imports if models ever reference API code.
    from warden_db.session import make_engine, make_sessionmaker

    engine = make_engine(settings.database_url.get_secret_value())
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    _log.info("db_pool_created", pool_size=10, max_overflow=5)

    # --- Redis client --------------------------------------------------
    # We create the client lazily: the client object is created now, but
    # it doesn't actually connect to Redis until the first command is sent.
    # This means the API boots successfully even if Redis is temporarily down.
    # decode_responses=True: Redis returns Python str instead of bytes,
    # which is more convenient for working with string keys and values.
    redis_client = aioredis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
    )
    app.state.redis = redis_client
    _log.info("redis_client_created")

    # --- Shared HTTP client --------------------------------------------
    # A single AsyncClient is shared across all requests.
    # timeout=10.0 means any individual HTTP request that takes longer than
    # 10 seconds will raise httpx.TimeoutException (the health check has
    # its own tighter 2-second deadline on top of this).
    http_client = httpx.AsyncClient(timeout=10.0)
    app.state.http_client = http_client
    _log.info("http_client_created")

    # Everything is set up - hand control to FastAPI to serve requests.
    yield

    # ------------------------------------------------------------------
    # SHUTDOWN (runs in reverse order - last created, first closed)
    # ------------------------------------------------------------------
    _log.info("shutdown_starting")

    # Close the HTTP client first (it doesn't depend on anything else).
    await http_client.aclose()
    _log.info("http_client_closed")

    # Close all Redis connections. aclose() sends QUIT to the server and
    # releases the underlying TCP connections back to the OS.
    await redis_client.aclose()
    _log.info("redis_client_closed")

    # Dispose the SQLAlchemy engine: closes all idle connections in the pool
    # and waits for active ones to finish. This prevents "connection leaked"
    # errors in Postgres's logs.
    await engine.dispose()
    _log.info("db_pool_disposed")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Optional Settings instance. If None, reads from environment.
                  Pass a Settings instance in tests to control configuration.

    Returns:
        Configured FastAPI application ready to serve requests.

    UVICORN FACTORY MODE:
    When running with `--factory`, uvicorn calls `create_app()` (no args)
    and uses the returned app. This allows graceful reloads because each
    worker creates its own app instance from scratch.
    """
    resolved_settings = settings or get_settings()

    # Set up structured logging before anything else.
    # This ensures all log messages (including startup logs) are formatted
    # correctly as JSON or colored console output.
    configure_logging(
        level=resolved_settings.log_level,
        json_logs=resolved_settings.json_logs,
    )

    app = FastAPI(
        title="WARDEN API",
        description="Web-change monitoring platform - API service",
        version="0.1.0",
        # Disable the automatic /docs and /redoc UIs in production by
        # checking json_logs (which is False in dev). In a real project you'd
        # check an explicit `environment` setting.
        # For now: always enable docs for ease of development.
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Store settings on app.state so lifespan and middleware can access it
    # without calling get_settings() again (which would re-read env vars).
    app.state.settings = resolved_settings

    # ---- Middleware -------------------------------------------------------
    # Middleware is applied in reverse order: the LAST one added is the
    # OUTERMOST wrapper (runs first on request, last on response).
    # We add RequestIDMiddleware once - it handles every request.
    app.add_middleware(RequestIDMiddleware)

    # ---- Routers ---------------------------------------------------------
    # Include the health endpoints. No prefix means:
    #   /live  -> GET /live
    #   /health -> GET /health
    #   /ready  -> GET /ready
    app.include_router(health_router)

    return app
