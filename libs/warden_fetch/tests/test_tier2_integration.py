"""Integration tests verifying Tier-2 Selenium Grid extraction on SPA pages."""

import pytest

from warden_fetch.tier2 import fetch_tier2


@pytest.mark.selenium
@pytest.mark.asyncio
async def test_tier2_extracts_spa_content_from_chaos_web():
    """Verify tier-2 Selenium Grid fetch executes JavaScript and extracts content

    that static HTTP fetch (tier-1) cannot extract from the chaos-web SPA archetype.
    """
    url = "http://chaos-web:8000/pages/app/cp3-verification"
    result = await fetch_tier2(
        url,
        selenium_hub_url="http://127.0.0.1:4444",
        extra_allowed_hosts=frozenset(["chaos-web"]),
    )

    assert result.status == 200
    assert result.rendered_html is not None
    assert len(result.rendered_html) > 0

    # JavaScript executed and replaced the loading spinner with the live dashboard
    assert b"Active Enterprise Dashboard" in result.rendered_html
    assert b"Initial Dashboard State" in result.rendered_html

    # Screenshot was captured via CDP
    assert result.screenshot is not None
    assert len(result.screenshot) > 1000
    # Valid PNG magic bytes
    assert result.screenshot.startswith(b"\x89PNG\r\n\x1a\n")
