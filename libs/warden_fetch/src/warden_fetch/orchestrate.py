"""Fetch orchestration routing between Tier-1 and Tier-2 with claim-check storage."""

from dataclasses import dataclass
from typing import Any

from warden_fetch.robots import is_fetch_allowed
from warden_fetch.tier1 import fetch_tier1
from warden_net.objectstore import ObjectStoreConfig, put_blob


@dataclass(frozen=True)
class MonitorFetchSpec:
    """Decoupled specification of a target monitor to fetch.

    ARCHITECTURE NOTE:
    Does NOT use the full SQLAlchemy Monitor ORM model to preserve complete decoupling
    between libs/warden_fetch and libs/warden_db.
    """

    url: str
    needs_render: bool = False
    etag: str | None = None
    last_modified: str | None = None
    selector_config: dict[str, Any] | None = None


@dataclass(frozen=True)
class FetchResult:
    """Outcome of fetch orchestration containing object storage claim-check references."""

    url: str
    status: int
    content_hash: str | None
    screenshot_hash: str | None
    not_modified: bool
    blocked_by_robots: bool
    tier_used: int
    elapsed_ms: float


async def fetch(
    spec: MonitorFetchSpec,
    *,
    objectstore_config: ObjectStoreConfig | None = None,
    extra_allowed_hosts: frozenset[str] = frozenset(),
    selenium_hub_url: str = "http://selenium-hub:4444",
    timeout_s: float = 15.0,
    max_bytes: int = 10_000_000,
) -> FetchResult:
    """Orchestrate fetching of a monitor specification with robots and tier routing."""
    # 1. Evaluate robots.txt policy
    robots_decision = await is_fetch_allowed(
        spec.url,
        extra_allowed_hosts=extra_allowed_hosts,
    )
    if not robots_decision.allowed:
        return FetchResult(
            url=spec.url,
            status=403,
            content_hash=None,
            screenshot_hash=None,
            not_modified=False,
            blocked_by_robots=True,
            tier_used=1,
            elapsed_ms=0.0,
        )

    # 2. Tier routing
    # TODO(prompt-5): auto-escalation heuristic (tier-1 succeeds but content looks JS-rendered)
    if spec.needs_render:
        from warden_fetch.tier2 import fetch_tier2

        t2_res = await fetch_tier2(
            spec.url,
            max_bytes=max_bytes,
            timeout_s=timeout_s,
            selenium_hub_url=selenium_hub_url,
            extra_allowed_hosts=extra_allowed_hosts,
        )

        content_hash = None
        screenshot_hash = None
        if objectstore_config:
            content_hash = await put_blob(
                objectstore_config,
                t2_res.rendered_html,
                content_type="text/html; charset=utf-8",
            )
            if t2_res.screenshot:
                screenshot_hash = await put_blob(
                    objectstore_config,
                    t2_res.screenshot,
                    content_type="image/png",
                )

        return FetchResult(
            url=spec.url,
            status=t2_res.status,
            content_hash=content_hash,
            screenshot_hash=screenshot_hash,
            not_modified=t2_res.not_modified,
            blocked_by_robots=False,
            tier_used=2,
            elapsed_ms=t2_res.elapsed_ms,
        )

    # Default: Tier-1 HTTP fetch
    t1_res = await fetch_tier1(
        spec.url,
        etag=spec.etag,
        last_modified=spec.last_modified,
        max_bytes=max_bytes,
        timeout_s=timeout_s,
        extra_allowed_hosts=extra_allowed_hosts,
    )

    content_hash = None
    if objectstore_config and t1_res.body:
        content_hash = await put_blob(
            objectstore_config,
            t1_res.body,
            content_type="text/html; charset=utf-8",
        )

    return FetchResult(
        url=spec.url,
        status=t1_res.status,
        content_hash=content_hash,
        screenshot_hash=None,
        not_modified=t1_res.not_modified,
        blocked_by_robots=False,
        tier_used=1,
        elapsed_ms=t1_res.elapsed_ms,
    )
