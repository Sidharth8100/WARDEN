"""Robots.txt parser and policy evaluator using Protego."""

import time
import urllib.parse
from dataclasses import dataclass

import httpx
from protego import Protego

from warden_fetch.tier1 import fetch_tier1

# WHY PROTEGO OVER STDLIB URLLIB.ROBOTPARSER?
# Python's built-in urllib.robotparser fails on modern robots.txt syntax:
# 1. It does not support standard wildcard '*' and end-of-URL '$' pattern
#    matching in Allow/Disallow rules.
# 2. It does not parse Crawl-delay or Request-rate directives reliably.
# Protego is a modern, RFC 9309 compliant robots.txt parser with complete wildcard support.


@dataclass(frozen=True)
class RobotsDecision:
    """Decision indicating whether fetching is allowed under robots.txt policy."""

    allowed: bool
    crawl_delay: float | None


# IN-PROCESS ROBOTS CACHE (TEMPORARY):
# Temporary in-process dict cache with manual timestamp check.
# Replaced by the hand-rolled LRU+TTL cache wired in on Day 8.
_ROBOTS_CACHE: dict[str, tuple[float, Protego]] = {}
_DEFAULT_TTL_S = 3600.0  # 1 hour cache TTL


def _get_robots_url(target_url: str) -> tuple[str, str]:
    """Extract origin and corresponding /robots.txt URL from target."""
    parsed = urllib.parse.urlsplit(target_url)
    origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    robots_url = f"{origin}/robots.txt"
    return origin, robots_url


def clear_robots_cache() -> None:
    """Clear cached robots.txt entries (for testing)."""
    _ROBOTS_CACHE.clear()


async def is_fetch_allowed(
    url: str,
    user_agent: str = "WardenBot",
    *,
    extra_allowed_hosts: frozenset[str] = frozenset(),
) -> RobotsDecision:
    """Evaluate robots.txt policy for a target URL and User-Agent.

    Robots.txt fetches are themselves executed via `fetch_tier1` and are therefore
    fully protected by SSRF validation. If /robots.txt is missing (404) or unreachable,
    the standard web crawler convention is followed: access is granted by default.
    """
    origin, robots_url = _get_robots_url(url)
    now = time.monotonic()

    # Check in-process cache
    cached = _ROBOTS_CACHE.get(origin)
    if cached is not None:
        cached_time, parser = cached
        if now - cached_time < _DEFAULT_TTL_S:
            allowed = parser.can_fetch(url, user_agent)
            delay = parser.crawl_delay(user_agent)
            return RobotsDecision(allowed=allowed, crawl_delay=delay)

    # Fetch /robots.txt via tier-1 (with SSRF check)
    try:
        tier1_res = await fetch_tier1(
            robots_url,
            timeout_s=5.0,
            extra_allowed_hosts=extra_allowed_hosts,
        )
        if tier1_res.status == 200:
            robots_content = tier1_res.body.decode("utf-8", errors="replace")
            parser = Protego.parse(robots_content)
        else:
            # 404 or other non-200 -> Standard web behavior: allow all
            parser = Protego.parse("")
    except (httpx.HTTPError, TimeoutError, Exception):
        # Unreachable robots.txt defaults to allowed per standard crawler convention
        parser = Protego.parse("")

    _ROBOTS_CACHE[origin] = (now, parser)
    allowed = parser.can_fetch(url, user_agent)
    delay = parser.crawl_delay(user_agent)
    return RobotsDecision(allowed=allowed, crawl_delay=delay)
