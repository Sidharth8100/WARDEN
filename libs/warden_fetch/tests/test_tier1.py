"""Unit tests for Tier-1 HTTP fetcher."""

from ipaddress import IPv4Address
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from warden_fetch.tier1 import fetch_tier1
from warden_net.ssrf_check import ResponseTooLargeError, SSRFBlockedError


@pytest.fixture(autouse=True)
def mock_dns_safe() -> None:
    with (
        patch("warden_net.ssrf_check.resolve_host", return_value=[IPv4Address("93.184.216.34")]),
        patch("warden_net.ssrf_check.is_blocked_ip", return_value=None),
    ):
        yield


@pytest.mark.asyncio
@respx.mock
async def test_fetch_tier1_success() -> None:
    respx.get("https://example.com/page").mock(
        return_value=Response(
            200,
            html="<html><body>Hello World</body></html>",
            headers={"ETag": '"abc"'},
        )
    )

    result = await fetch_tier1("https://example.com/page")
    assert result.status == 200
    assert result.not_modified is False
    assert b"Hello World" in result.body
    assert result.headers.get("etag") == '"abc"'
    assert result.elapsed_ms > 0


@pytest.mark.asyncio
@respx.mock
async def test_fetch_tier1_not_modified() -> None:
    respx.get("https://example.com/page").mock(return_value=Response(304))

    result = await fetch_tier1("https://example.com/page", etag='"abc"')
    assert result.status == 304
    assert result.not_modified is True
    assert result.body == b""


@pytest.mark.asyncio
async def test_fetch_tier1_ssrf_blocked() -> None:
    with (
        patch("warden_net.ssrf_check.is_blocked_ip", return_value="private"),
        pytest.raises(SSRFBlockedError),
    ):
        await fetch_tier1("https://private.internal/page")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_tier1_max_bytes_exceeded() -> None:
    large_payload = b"X" * 2000
    respx.get("https://example.com/large").mock(return_value=Response(200, content=large_payload))

    with pytest.raises(ResponseTooLargeError):
        await fetch_tier1("https://example.com/large", max_bytes=500)
