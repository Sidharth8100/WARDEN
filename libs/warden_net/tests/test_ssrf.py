"""Tests for SSRF check and safe_get in warden_net."""

from ipaddress import IPv4Address
from unittest.mock import AsyncMock, patch

import pytest
import respx

from warden_net.ssrf_check import (
    ResponseTooLargeError,
    SSRFBlockedError,
    TooManyRedirectsError,
    safe_get,
    validate_url_is_safe,
)


@pytest.mark.asyncio
async def test_safe_url_passes():
    """Verify safe URL passes validation and returns response body."""
    with (
        patch("warden_net.ssrf_check.resolve_host", new_callable=AsyncMock) as mock_resolve,
        patch("warden_net.ssrf_check.is_blocked_ip", return_value=None),
    ):
        mock_resolve.return_value = [IPv4Address("93.184.216.34")]

        with respx.mock(base_url="http://example.com") as respx_mock:
            respx_mock.get("/").respond(200, html="<h1>Hello World</h1>")

            result = await safe_get("http://example.com/", max_bytes=1024)
            assert result.status_code == 200
            assert result.content == b"<h1>Hello World</h1>"
            assert result.hop_count == 0


@pytest.mark.asyncio
async def test_private_ip_rejected_before_request():
    """Verify URL resolving to a private IP is rejected before any HTTP request is issued."""
    with (
        patch("warden_net.ssrf_check.resolve_host", new_callable=AsyncMock) as mock_resolve,
        patch("warden_net.ssrf_check.is_blocked_ip", return_value="private"),
    ):
        mock_resolve.return_value = [IPv4Address("192.168.1.1")]

        with respx.mock(assert_all_called=False) as respx_mock:
            route = respx_mock.get("http://internal.service/").respond(200)

            with pytest.raises(SSRFBlockedError, match="SSRF check failed"):
                await safe_get("http://internal.service/")

            assert not route.called


@pytest.mark.asyncio
async def test_redirect_chain_caught_at_second_hop():
    """Verify safe host redirecting to internal blocked IP is blocked at hop 1."""

    async def fake_resolve(host: str):
        if host == "safe.com":
            return [IPv4Address("93.184.216.34")]
        if host == "metadata.internal":
            return [IPv4Address("169.254.169.254")]
        return [IPv4Address("93.184.216.34")]

    def fake_is_blocked(ip):
        if str(ip) == "169.254.169.254":
            return "cloud_metadata"
        return None

    with (
        patch("warden_net.ssrf_check.resolve_host", side_effect=fake_resolve),
        patch("warden_net.ssrf_check.is_blocked_ip", side_effect=fake_is_blocked),
        respx.mock(assert_all_called=False) as respx_mock,
    ):
        respx_mock.get("http://safe.com/start").respond(
            302, headers={"Location": "http://metadata.internal/latest/meta-data/"}
        )
        metadata_route = respx_mock.get("http://metadata.internal/latest/meta-data/").respond(
            200, text="SECRET_KEY"
        )

        with pytest.raises(SSRFBlockedError, match="SSRF check failed at hop 1"):
            await safe_get("http://safe.com/start")

        assert not metadata_route.called


@pytest.mark.asyncio
async def test_redirect_chain_longer_than_5_hops_rejected():
    """Verify redirect chains exceeding 5 hops are rejected."""
    with (
        patch("warden_net.ssrf_check.resolve_host", new_callable=AsyncMock) as mock_resolve,
        patch("warden_net.ssrf_check.is_blocked_ip", return_value=None),
    ):
        mock_resolve.return_value = [IPv4Address("93.184.216.34")]

        with respx.mock(assert_all_called=False) as respx_mock:
            for i in range(1, 8):
                respx_mock.get(f"http://example.com/step{i}").respond(
                    302, headers={"Location": f"http://example.com/step{i + 1}"}
                )

            with pytest.raises(TooManyRedirectsError, match="Exceeded maximum of 5 redirect hops"):
                await safe_get("http://example.com/step1")


@pytest.mark.asyncio
async def test_response_exceeding_max_bytes_aborts_mid_stream():
    """Verify response streaming aborts mid-stream when max_bytes is exceeded."""
    chunks_sent = 0

    async def large_content_stream():
        nonlocal chunks_sent
        for _ in range(10):
            chunks_sent += 1
            yield b"X" * 1024  # 1 KB per chunk

    with (
        patch("warden_net.ssrf_check.resolve_host", new_callable=AsyncMock) as mock_resolve,
        patch("warden_net.ssrf_check.is_blocked_ip", return_value=None),
    ):
        mock_resolve.return_value = [IPv4Address("93.184.216.34")]

        with respx.mock() as respx_mock:
            respx_mock.get("http://example.com/large").respond(
                200,
                stream=large_content_stream(),
            )

            # Max allowed is 2500 bytes (~2.5 KB); 10 KB available
            msg = "Response exceeded maximum allowed bytes"
            with pytest.raises(ResponseTooLargeError, match=msg):
                await safe_get("http://example.com/large", max_bytes=2500)

            # Assert generator did not send all 10 chunks (stream aborted early)
            assert chunks_sent < 10


@pytest.mark.student_todo
@pytest.mark.asyncio
async def test_literal_decimal_ip_rejected():
    """Verify literal decimal integer IP URL (e.g. 2130706433 for 127.0.0.1) is rejected.

    Tagged @pytest.mark.student_todo because it exercises parse_ip_literal and is_blocked_ip stubs.
    """
    result = await validate_url_is_safe("http://2130706433/")
    assert not result.safe
    assert result.reason in ("loopback", "private")
