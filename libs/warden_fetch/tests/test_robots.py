"""Unit tests for robots.txt parsing and policy checks."""

from ipaddress import IPv4Address
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from warden_fetch.robots import clear_robots_cache, is_fetch_allowed


@pytest.fixture(autouse=True)
def setup_robots_test() -> None:
    clear_robots_cache()
    with (
        patch("warden_net.ssrf_check.resolve_host", return_value=[IPv4Address("93.184.216.34")]),
        patch("warden_net.ssrf_check.is_blocked_ip", return_value=None),
    ):
        yield


@pytest.mark.asyncio
@respx.mock
async def test_robots_allowed_and_disallowed() -> None:
    robots_txt = """
    User-agent: *
    Disallow: /admin/
    Disallow: /private/
    Allow: /admin/public/
    Crawl-delay: 2.5
    """
    respx.get("https://example.com/robots.txt").mock(return_value=Response(200, text=robots_txt))

    # Disallowed path
    res_disallowed = await is_fetch_allowed("https://example.com/admin/settings")
    assert res_disallowed.allowed is False
    assert res_disallowed.crawl_delay == 2.5

    # Explicitly allowed subpath
    res_allowed = await is_fetch_allowed("https://example.com/admin/public/info")
    assert res_allowed.allowed is True

    # General allowed path
    res_general = await is_fetch_allowed("https://example.com/blog/posts")
    assert res_general.allowed is True


@pytest.mark.asyncio
@respx.mock
async def test_robots_404_defaults_to_allowed() -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=Response(404))

    result = await is_fetch_allowed("https://example.com/secret")
    assert result.allowed is True
    assert result.crawl_delay is None
