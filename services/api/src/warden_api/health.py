"""Health check endpoints for the WARDEN API.

THREE ENDPOINTS - THREE DIFFERENT PURPOSES:
============================================

GET /live  - "Is the process alive?"
  The simplest possible check. Used by Docker/Kubernetes to know if the
  container is still running and hasn't crashed. It intentionally touches
  NO external dependencies. If the process can respond to HTTP at all,
  it's "live". Returns 200 immediately.

  WHY does the Dockerfile HEALTHCHECK use /live and not /health?
  Because during startup, the database migration hasn't run yet, so /health
  would return 503. Docker would think the container is unhealthy and kill it,
  preventing it from ever becoming healthy. /live stays 200 the whole time.

GET /health - "Are all dependencies reachable?"
  Checks Postgres, Redis, and the object store concurrently. Each check has
  a 2-second timeout. Returns {"status": "ok"} with HTTP 200 if everything
  works, or {"status": "degraded"} with HTTP 503 if anything fails.

  This endpoint is for OPERATORS - they use it to debug "is the API down
  because the database is unreachable?" vs "is the code itself broken?".

  IMPORTANT: This endpoint NEVER reveals connection URLs or passwords in error
  messages - only generic descriptions of what failed.

GET /ready - "Is this instance ready to serve traffic?"
  Checks if Alembic migrations are up to date. Returns 503 if the database
  schema is behind the code. This is a safety net: if you deploy new code
  but forget to run migrations, /ready returns 503 so the load balancer
  stops routing traffic to this instance until migrations run.

CONCURRENCY WITH asyncio.gather():
====================================
asyncio.gather() runs multiple coroutines at the SAME TIME (concurrently).
Without it, the three health checks would run one after another:
  - postgres check: up to 2s
  - redis check: up to 2s
  - objectstore check: up to 2s
  Total worst case: 6 seconds

With asyncio.gather(), all three run at the same time:
  Total worst case: 2 seconds (the slowest one)

asyncio.wait_for(coroutine, timeout=2) wraps a check with a deadline.
If the check takes more than 2 seconds, it raises asyncio.TimeoutError,
which we catch and report as a failure.
"""

import asyncio
import time
from collections.abc import Coroutine
from typing import Any

import httpx
import structlog
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from warden_api.settings import get_settings

_log = structlog.get_logger(__name__)

# APIRouter is FastAPI's way of grouping related endpoints.
# We create a router here and include it in main.py (the app factory).
# This keeps health endpoints in their own file without importing the full app.
router = APIRouter()


# ---------------------------------------------------------------------------
# /live  - liveness probe (no dependencies)
# ---------------------------------------------------------------------------


@router.get("/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    """Always returns 200. Proves the process is running.

    Does NOT check any external dependency - this is intentional.
    See module docstring for explanation of why.
    """
    return {"status": "alive"}


# ---------------------------------------------------------------------------
# Helper: individual dependency check functions
# ---------------------------------------------------------------------------
# Each function returns a dict: {"ok": bool, "ms": float, "error": str | None}
# They NEVER raise - all exceptions are caught and returned as error strings.
# Error strings NEVER contain URLs or passwords.


async def _check_postgres(request: Request) -> dict[str, Any]:
    """Check if Postgres is reachable by running SELECT 1."""
    start = time.perf_counter()
    try:
        # We borrow ONE connection from the pool to run a cheap query.
        # "SELECT 1" is the lightest possible Postgres health check.
        async with request.app.state.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
        return {"ok": True, "ms": round((time.perf_counter() - start) * 1000, 2)}
    except SQLAlchemyError as exc:
        # Log the real error internally for debugging, but don't expose it
        # in the response (it might contain the connection URL or credentials).
        _log.warning("postgres_check_failed", error=str(exc))
        return {
            "ok": False,
            "ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "postgres unreachable",
        }
    except Exception as exc:
        _log.warning("postgres_check_error", error=str(exc))
        return {
            "ok": False,
            "ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "postgres check failed",
        }


async def _check_redis(request: Request) -> dict[str, Any]:
    """Check if Redis is reachable by sending PING."""
    start = time.perf_counter()
    try:
        # redis.asyncio client's ping() sends the Redis PING command.
        # Redis responds with PONG if it's alive.
        await request.app.state.redis.ping()
        return {"ok": True, "ms": round((time.perf_counter() - start) * 1000, 2)}
    except Exception as exc:
        _log.warning("redis_check_failed", error=str(exc))
        return {
            "ok": False,
            "ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "redis unreachable",
        }


async def _check_objectstore(request: Request) -> dict[str, Any]:
    """Check if the object store (SeaweedFS S3) is reachable.

    ENDPOINT DISCOVERY (recorded in docs/reports/prompt-01.md):
    We tested several endpoints inside the running SeaweedFS container.
    The S3 API root at / (port 8333) returned:
      HTTP 200 with an XML ListAllMyBucketsResult response.
    This is the most appropriate health check endpoint because:
      - It's the same port/protocol the API will use for all S3 operations
      - It responds even when no buckets exist (which is the initial state)
      - It doesn't require authentication in the default SeaweedFS config
    """
    start = time.perf_counter()
    try:
        # Use the shared httpx client created during app startup (in main.py).
        # A shared client reuses TCP connections (connection pooling), which
        # is more efficient than creating a new client on every health check.
        settings = get_settings()
        response = await request.app.state.http_client.get(settings.objectstore_url)
        # SeaweedFS S3 returns 200 for GET / (list buckets).
        # Any 2xx response means the objectstore is reachable.
        ok = response.status_code < 400
        return {
            "ok": ok,
            "ms": round((time.perf_counter() - start) * 1000, 2),
            **({"error": f"http {response.status_code}"} if not ok else {}),
        }
    except httpx.ConnectError:
        _log.warning("objectstore_check_failed", reason="connection refused")
        return {
            "ok": False,
            "ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "objectstore unreachable",
        }
    except Exception as exc:
        _log.warning("objectstore_check_error", error=str(exc))
        return {
            "ok": False,
            "ms": round((time.perf_counter() - start) * 1000, 2),
            "error": "objectstore check failed",
        }


# ---------------------------------------------------------------------------
# /health  - dependency health check (concurrent, with timeouts)
# ---------------------------------------------------------------------------


@router.get("/health", summary="Dependency health check")
async def health(request: Request) -> JSONResponse:
    """Check all three dependencies concurrently with 2-second timeouts each.

    Returns:
      200 {"status": "ok", "checks": {...}}     if all checks pass
      503 {"status": "degraded", "checks": {...}} if any check fails
    """

    async def _safe(coro: "Coroutine[Any, Any, dict[str, Any]]") -> dict[str, Any]:
        """Wrap a check coroutine with a 2-second timeout.

        If the check times out, we return a failed result instead of raising.
        This means /health always responds quickly, even if dependencies hang.
        """
        try:
            return await asyncio.wait_for(coro, timeout=2.0)
        except TimeoutError:
            return {"ok": False, "ms": 2000.0, "error": "timed out after 2s"}

    # Run all three checks at the SAME TIME using asyncio.gather().
    # gather() returns a list in the same order as the input coroutines.
    pg_result, redis_result, obj_result = await asyncio.gather(
        _safe(_check_postgres(request)),
        _safe(_check_redis(request)),
        _safe(_check_objectstore(request)),
    )

    checks = {
        "postgres": pg_result,
        "redis": redis_result,
        "objectstore": obj_result,
    }

    all_ok = all(c["ok"] for c in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# /ready  - readiness probe (checks if migrations are current)
# ---------------------------------------------------------------------------


@router.get("/ready", summary="Readiness probe (migrations check)")
async def ready(request: Request) -> JSONResponse:
    """Return 200 only if the database schema matches the current code.

    Compares the alembic_version table in the database (what migrations
    have actually been applied) against the head revision in the migration
    scripts (what the current code expects).

    WHY IS THIS IMPORTANT?
    When you deploy new code before running migrations, the code might try
    to access columns or tables that don't exist yet. /ready returning 503
    signals to the load balancer to stop sending traffic here until the
    migration (the `migrate` service) finishes.
    """
    settings = get_settings()

    # ----------------------------------------------------------------
    # Step 1: Find the "head" revision from the migration scripts.
    # ----------------------------------------------------------------
    # The head revision is the latest migration the CODE expects to be applied.
    try:
        alembic_cfg = AlembicConfig(settings.alembic_ini_path)
        script_dir = ScriptDirectory.from_config(alembic_cfg)
        # get_current_head() returns the hash of the latest migration script,
        # e.g. "a3f2b1c4d5e6" (the file named a3f2b1c4d5e6_initial_schema.py)
        head_rev: str | None = script_dir.get_current_head()
    except Exception as exc:
        _log.warning("ready_alembic_config_error", error=str(exc))
        return JSONResponse(
            content={"status": "not_ready", "detail": "cannot read alembic config"},
            status_code=503,
        )

    # ----------------------------------------------------------------
    # Step 2: Read the current revision from the database.
    # ----------------------------------------------------------------
    # The alembic_version table has one row with the hash of the last
    # migration that was successfully applied to this database.
    db_rev: str | None = None
    try:
        async with request.app.state.sessionmaker() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.scalar_one_or_none()
            db_rev = str(row) if row is not None else None
    except SQLAlchemyError as exc:
        # If the alembic_version table doesn't exist yet (fresh database,
        # no migrations run), we get an error here. We treat this as "not ready".
        _log.warning("ready_db_check_error", error=str(exc))
        return JSONResponse(
            content={
                "status": "not_ready",
                "detail": "alembic_version table missing or unreadable",
                "head": head_rev,
                "current": None,
            },
            status_code=503,
        )

    # ----------------------------------------------------------------
    # Step 3: Compare and respond.
    # ----------------------------------------------------------------
    if db_rev == head_rev:
        return JSONResponse(
            content={"status": "ready", "revision": db_rev},
            status_code=200,
        )
    else:
        _log.warning("ready_schema_mismatch", head=head_rev, current=db_rev)
        return JSONResponse(
            content={
                "status": "not_ready",
                "detail": "database schema is behind code",
                "head": head_rev,  # what the code expects
                "current": db_rev,  # what the DB actually has
            },
            status_code=503,
        )
