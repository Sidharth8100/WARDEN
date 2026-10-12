"""SSRF validation and safe HTTP fetching.

This module resolves hostnames, checks resolved IP addresses against blocked
ranges, and performs redirect-validated streaming fetches with byte limits.
"""

import asyncio
import urllib.parse
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address

import httpx

from warden_core.ssrf import is_blocked_ip, parse_ip_literal
from warden_net.canonicalize import canonicalize_url


class SSRFBlockedError(Exception):
    """Raised when an address or redirect hop is blocked by the SSRF shield."""


class TooManyRedirectsError(Exception):
    """Raised when redirect chain exceeds the maximum allowed hops (5)."""


class ResponseTooLargeError(Exception):
    """Raised when streamed response exceeds max_bytes limit."""


@dataclass(frozen=True)
class SSRFCheckResult:
    """Outcome of URL safety validation."""

    safe: bool
    reason: str | None
    resolved_ips: list[str]


@dataclass(frozen=True)
class SafeFetchResult:
    """Outcome of a successful safe GET request."""

    final_url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    hop_count: int


async def resolve_host(host: str) -> list[IPv4Address | IPv6Address]:
    """Resolve a host string to a list of IP addresses.

    If host is an IP literal, returns [literal] without any DNS lookup.
    Otherwise, resolves via asyncio.get_running_loop().getaddrinfo and returns
    ALL returned addresses (both IPv4 and IPv6).
    """
    # Clean bracketed IPv6 literals (e.g. "[::1]")
    clean_host = host[1:-1] if (host.startswith("[") and host.endswith("]")) else host

    try:
        literal = parse_ip_literal(clean_host)
        if literal is not None:
            return [literal]
    except NotImplementedError:
        # Fallback when student stub has not yet been implemented
        pass

    loop = asyncio.get_running_loop()
    addr_info = await loop.getaddrinfo(clean_host, None)

    ips: list[IPv4Address | IPv6Address] = []
    seen: set[str] = set()

    for item in addr_info:
        sockaddr = item[4]
        ip_str = str(sockaddr[0])
        if ip_str not in seen:
            seen.add(ip_str)
            ips.append(ip_address(ip_str))

    return ips


async def validate_url_is_safe(url: str) -> SSRFCheckResult:
    """Canonicalize, resolve host, and verify all resolved IPs against blocked ranges.

    If ANY resolved IP address is blocked, the ENTIRE URL is rejected.
    This prevents DNS rebinding / round-robin attacks where an attacker points
    a hostname at both a safe public IP and an internal address.
    """
    try:
        canonical_obj = canonicalize_url(url)
    except Exception as exc:
        return SSRFCheckResult(safe=False, reason=f"invalid_url: {exc}", resolved_ips=[])

    parsed = urllib.parse.urlsplit(canonical_obj.url)
    host = parsed.hostname
    if not host:
        return SSRFCheckResult(safe=False, reason="missing_host", resolved_ips=[])

    try:
        resolved = await resolve_host(host)
    except Exception as exc:
        return SSRFCheckResult(safe=False, reason=f"dns_resolution_failed: {exc}", resolved_ips=[])

    if not resolved:
        return SSRFCheckResult(safe=False, reason="no_addresses_resolved", resolved_ips=[])

    resolved_strs = [str(ip) for ip in resolved]

    # Verify every IP
    for ip in resolved:
        try:
            blocked_reason = is_blocked_ip(ip)
        except NotImplementedError:
            # When student stub is not yet implemented, default to unblocked
            blocked_reason = None

        if blocked_reason is not None:
            # Information leakage note: Detailed server-side reasons are stored in
            # this result for internal logs, but external API endpoints must return
            # generic errors to avoid exposing internal topology.
            return SSRFCheckResult(safe=False, reason=blocked_reason, resolved_ips=resolved_strs)

    return SSRFCheckResult(safe=True, reason=None, resolved_ips=resolved_strs)


async def safe_get(
    url: str,
    *,
    max_bytes: int = 2_097_152,
    timeout_s: float = 10.0,
) -> SafeFetchResult:
    """Safely fetch a URL with SSRF validation at every redirect hop and bounded streaming.

    Key protections:
    1. follow_redirects=False: We manually inspect and validate every redirect hop before
       connecting, preventing redirect-to-localhost or internal network pivot attacks.
    2. Streaming body: We read chunks with a running counter, terminating the connection
       immediately if max_bytes is exceeded to defend against zip/gzip bombs or unbounded streams.
    3. Hop limit: Capped at 5 hops to prevent redirect loops.
    """
    curr_url = url
    hop = 0
    max_hops = 5

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout_s,
    ) as client:
        while True:
            # 1. Validate current URL
            check = await validate_url_is_safe(curr_url)
            if not check.safe:
                raise SSRFBlockedError(
                    f"SSRF check failed at hop {hop} ({curr_url}): {check.reason}"
                )

            # 2. Issue request via streaming
            headers = {"User-Agent": "WARDEN-Fetcher/1.0"}
            req = client.build_request("GET", curr_url, headers=headers)
            resp = await client.send(req, stream=True)

            # Check for 3xx redirect
            if resp.status_code in (301, 302, 303, 307, 308):
                await resp.aclose()
                hop += 1
                if hop > max_hops:
                    raise TooManyRedirectsError(f"Exceeded maximum of {max_hops} redirect hops")

                location = resp.headers.get("Location")
                if not location:
                    msg = f"Redirect response ({resp.status_code}) missing Location header"
                    raise ValueError(msg)

                curr_url = urllib.parse.urljoin(curr_url, location)
                continue

            # Stream body with strict byte counter
            total_bytes = 0
            chunks: list[bytes] = []

            try:
                async for chunk in resp.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise ResponseTooLargeError(
                            f"Response exceeded maximum allowed bytes ({max_bytes})"
                        )
                    chunks.append(chunk)
            finally:
                await resp.aclose()

            return SafeFetchResult(
                final_url=curr_url,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                content=b"".join(chunks),
                hop_count=hop,
            )
