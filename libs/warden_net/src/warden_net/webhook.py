"""HTTPS POST bound to the existing SSRF decision, with no proxy or redirect path."""

import asyncio
import http.client
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from warden_net.canonicalize import canonicalize_url
from warden_net.ssrf_check import SSRFBlockedError, validate_url_is_safe


@dataclass(frozen=True)
class HttpResult:
    status: int
    retry_after: str | None = None


def _post_pinned(
    url: str, address: str, body: bytes, headers: dict[str, str], timeout_s: float
) -> HttpResult:
    target = urlsplit(url)
    host = target.hostname
    if host is None:
        raise ValueError("missing host")
    deadline = time.monotonic() + timeout_s
    connection = http.client.HTTPConnection(host, port=target.port or 443, timeout=timeout_s)
    raw = socket.create_connection((address, target.port or 443), timeout=timeout_s)
    try:
        connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        path = (target.path or "/") + ("?" + target.query if target.query else "")
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("webhook_deadline")
            connection.sock.settimeout(remaining)
            chunk = response.read(min(8192, 65537 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > 65536:
                raise ValueError("webhook_response_limit")
        return HttpResult(response.status, response.getheader("Retry-After"))
    finally:
        connection.close()
        raw.close()


async def safe_post(
    url: str, body: bytes, headers: dict[str, str], *, timeout_s: float = 10
) -> HttpResult:
    if len(body) > 65536 or not 0 < timeout_s <= 15:
        raise ValueError("webhook_request_limit")
    if any(ord(c) <= 32 for c in url):
        raise SSRFBlockedError("invalid_url_characters")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise SSRFBlockedError("webhook_requires_https_without_userinfo_or_fragment")
    canonical = canonicalize_url(url).url
    async with asyncio.timeout(timeout_s):
        decision = await validate_url_is_safe(canonical)
        if not decision.safe or not decision.resolved_ips:
            raise SSRFBlockedError("webhook_destination_blocked")
        return await asyncio.to_thread(
            _post_pinned,
            canonical,
            decision.resolved_ips[0],
            body,
            {**headers, "Content-Type": "application/json", "User-Agent": "WARDEN-Webhook/1"},
            timeout_s,
        )
