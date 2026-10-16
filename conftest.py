"""Root conftest.py: global fixtures and test marker auto-skip hooks."""

import urllib.request

import pytest


def _is_url_reachable(url: str, timeout: float = 0.5) -> bool:
    """Check if an HTTP endpoint responds with a 2xx status code."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except Exception:
        return False


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Auto-skip tests tagged with infrastructure markers if unreachable."""
    if item.get_closest_marker("selenium") and not _is_url_reachable(
        "http://127.0.0.1:4444/status"
    ):
        pytest.skip("Selenium Grid hub on 127.0.0.1:4444 is unreachable")
    if item.get_closest_marker("chaos_web") and not _is_url_reachable(
        "http://127.0.0.1:8000/health"
    ):
        pytest.skip("chaos-web service is unreachable")
