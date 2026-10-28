"""No webhook private-host exceptions; validated IP is the connection IP."""

from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

import pytest

from warden_net.ssrf_check import SSRFBlockedError
from warden_net.webhook import HttpResult, safe_post


@pytest.mark.parametrize(
    "target",
    [
        "http://example.org/hook",
        "https://localhost/hook",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest",
        "https://user:password@example.org/",
        "https://example.org/#fragment",
        "https://[::1]/",
    ],
)
async def test_rejects_private_and_unsafe_webhook_targets(target: str) -> None:
    with pytest.raises(SSRFBlockedError):
        await safe_post(target, b"{}", {})


async def test_pin_and_no_second_dns_resolution() -> None:
    public = ip_address("93.184.216.34")
    with (
        patch("warden_net.ssrf_check.resolve_host", new=AsyncMock(return_value=[public])) as dns,
        patch("warden_net.webhook._post_pinned", return_value=HttpResult(307)) as post,
    ):
        result = await safe_post("https://alerts.example.org/hook", b"{}", {"Webhook-Id": "id"})
    assert result.status == 307  # caller treats redirect as terminal, no follow-up request
    dns.assert_awaited_once()
    assert post.call_args.args[1] == str(public)
    post.assert_called_once()


async def test_mixed_dns_and_limits() -> None:
    with (
        patch(
            "warden_net.ssrf_check.resolve_host",
            new=AsyncMock(return_value=[ip_address("93.184.216.34"), ip_address("10.0.0.1")]),
        ),
        pytest.raises(SSRFBlockedError),
    ):
        await safe_post("https://alerts.example.org/hook", b"{}", {})
    with pytest.raises(ValueError):
        await safe_post("https://example.org/", b"x" * 65537, {})
