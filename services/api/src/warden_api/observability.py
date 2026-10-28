"""HTTP entry spans and a durable-receipt SLO sampler; no user labels in metrics."""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from warden_observe.telemetry import (
    BACKLOG,
    LATENCY,
    OLDEST,
    OPERATIONS,
    SLO_BAD,
    SLO_SAMPLE_TIME,
    SLO_TOTAL,
    span,
)

from warden_db.models import CrawlJob


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def traced(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.monotonic()
        with span("api.request", request.headers.get("traceparent")):
            response = await call_next(request)
            OPERATIONS.labels("api", "error" if response.status_code >= 500 else "success").inc()
            LATENCY.labels("api").observe(time.monotonic() - started)
            return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics_response() -> Response:
        return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


async def sample(sessions: async_sessionmaker[AsyncSession], *, deadline_s: float = 120) -> None:
    now = datetime.now(UTC)
    async with sessions() as session:
        queued, earliest = (
            await session.execute(
                select(func.count(), func.min(CrawlJob.created_at)).where(
                    CrawlJob.status == "queued"
                )
            )
        ).one()
        BACKLOG.set(queued)
        OLDEST.set(max(0, (now - earliest).total_seconds()) if earliest else 0)
        for name, seconds in (("5m", 300), ("1h", 3600), ("30m", 1800), ("6h", 21600)):
            window = (
                CrawlJob.created_at >= now - timedelta(seconds=seconds),
                CrawlJob.created_at <= now - timedelta(seconds=deadline_s),
            )
            total = (
                await session.scalar(select(func.count()).select_from(CrawlJob).where(*window)) or 0
            )
            bad = (
                await session.scalar(
                    select(func.count())
                    .select_from(CrawlJob)
                    .where(
                        *window,
                        or_(
                            CrawlJob.status == "failed",
                            CrawlJob.completed_at.is_(None),
                            CrawlJob.completed_at - CrawlJob.created_at
                            > timedelta(seconds=deadline_s),
                        ),
                    )
                )
                or 0
            )
            SLO_TOTAL.labels(name).set(total)
            SLO_BAD.labels(name).set(bad / total if total else 0)
        SLO_SAMPLE_TIME.set(time.time())


async def sampler(sessions: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            await sample(
                sessions, deadline_s=float(os.environ.get("WARDEN_CRAWL_SLO_SECONDS", "120"))
            )
        except (OSError, TimeoutError, SQLAlchemyError):
            OPERATIONS.labels("slo_sampler", "error").inc()
        await asyncio.sleep(5)
