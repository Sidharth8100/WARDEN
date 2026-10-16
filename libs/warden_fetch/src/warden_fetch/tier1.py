"""Tier-1 HTTP fetcher using SSRF-safe streaming and conditional headers."""

import hashlib
import time
from dataclasses import dataclass

import httpx
import structlog
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from warden_net.ssrf_check import SafeFetchResult, safe_get

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Tier1Result:
    """Outcome of a Tier-1 HTTP fetch."""

    status: int
    final_url: str
    headers: dict[str, str]
    body: bytes
    not_modified: bool
    elapsed_ms: float


def _stop_within_total_timeout(timeout_s: float) -> stop_after_attempt:
    """Stop condition ensuring max 3 attempts within the total retry budget.

    RETRY SCOPE DISTINCTION:
    This is an in-process, network-level retry for transient socket errors (timeouts,
    connection resets). It is completely distinct from the job-level retry system
    Prompt 5 will build, which schedules entire monitor check jobs across workers.
    """
    return stop_after_attempt(3)


async def fetch_tier1(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    max_bytes: int = 10_000_000,
    timeout_s: float = 15.0,
    extra_allowed_hosts: frozenset[str] = frozenset(),
) -> Tier1Result:
    """Safely fetch a URL using Tier-1 HTTP streaming with retry and conditional headers.

    Never bypasses or reimplements SSRF validation: delegates directly to `warden_net.safe_get`.
    """
    start_time = time.monotonic()
    url_hash = hashlib.sha256(url.encode()).hexdigest()

    extra_headers: dict[str, str] = {}
    if etag:
        extra_headers["If-None-Match"] = etag
    if last_modified:
        extra_headers["If-Modified-Since"] = last_modified

    # Calculate remaining time budget per attempt
    def _before_attempt(retry_state: RetryCallState) -> None:
        elapsed = time.monotonic() - start_time
        if elapsed >= timeout_s:
            msg = (
                f"Overall fetch budget of {timeout_s}s exceeded before attempt "
                f"{retry_state.attempt_number}"
            )
            raise httpx.ConnectTimeout(msg)

    retry_exc = (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError)

    @retry(
        retry=retry_if_exception_type(retry_exc),
        wait=wait_random_exponential(multiplier=0.1, max=1.0),
        stop=_stop_within_total_timeout(timeout_s),
        before=_before_attempt,
        reraise=True,
    )
    async def _execute_fetch() -> SafeFetchResult:
        remaining_s = max(0.1, timeout_s - (time.monotonic() - start_time))
        return await safe_get(
            url,
            max_bytes=max_bytes,
            timeout_s=remaining_s,
            extra_allowed_hosts=extra_allowed_hosts,
            extra_headers=extra_headers if extra_headers else None,
        )

    fetch_res = await _execute_fetch()
    elapsed_ms = (time.monotonic() - start_time) * 1000.0

    not_modified = fetch_res.status_code == 304
    body = b"" if not_modified else fetch_res.content

    # PROMPT 6 NOTE: These structlog timing events will be turned into Prometheus
    # histogram observations in Prompt 6.
    _log.info(
        "fetch.tier1",
        url_hash=url_hash,
        status=fetch_res.status_code,
        elapsed_ms=round(elapsed_ms, 2),
        not_modified=not_modified,
    )

    return Tier1Result(
        status=fetch_res.status_code,
        final_url=fetch_res.final_url,
        headers=fetch_res.headers,
        body=body,
        not_modified=not_modified,
        elapsed_ms=elapsed_ms,
    )
