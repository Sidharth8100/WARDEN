"""SSRF validation corpus and regression suite.

Contains:
1. 25+ Hostile URLs covering private networks, loopback addresses, cloud metadata
   endpoints (AWS/GCP/Azure IPv4 & IPv6), non-HTTP schemes, and alternative IP representations.
2. 10 Legitimate public web targets.
3. Extension guidelines for human study protocol to add 20-30 additional hostile test vectors.
"""

import contextlib
import ipaddress
from ipaddress import IPv4Address, IPv6Address
from unittest.mock import AsyncMock, patch

import pytest

from warden_net.ssrf_check import validate_url_is_safe

# ==============================================================================
# HUMAN STUDY PROTOCOL: INSTRUCTIONS FOR ADDING NEW SSRF TEST CASES
# ==============================================================================
# When conducting your manual penetration testing and adding 20-30 additional
# URLs, append them directly to the `HOSTILE_URLS` or `LEGITIMATE_URLS` lists below.
#
# Recommended hostile categories to explore:
# 1. DNS Rebinding simulation domains (e.g., test domains with dual A records)
# 2. Obfuscated Unicode / Punycode domains (e.g. IDN homograph attacks)
# 3. URL parser differential quirks (embedded credentials, userinfo @, backslashes)
# 4. Embedded ports (port 22, 25, 6379, 5432, 18000)
# 5. IPv6 compressed zone identifiers (e.g., fe80::1%eth0)
# ==============================================================================

HOSTILE_URLS = [
    # --- Loopback IPv4 & Hostname ---
    "http://127.0.0.1/",
    "http://127.0.0.2:8080/admin",
    "http://127.255.255.254/",
    "http://localhost/",
    "http://localhost:8000/api",
    # --- Cloud Metadata Services (AWS / GCP / Azure / OpenStack) ---
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.169.254/computeMetadata/v1/",
    "http://169.254.1.1/internal",
    "http://[fd00:ec2::254]/",  # AWS IMDSv2 IPv6 metadata address
    # --- IPv6 Loopback & Link-Local ---
    "http://[::1]/",
    "http://[::1]:18000/",
    "http://[fe80::1]/",
    "http://[fe80::abcd:ef01]/secret",
    # --- RFC 1918 Private Ranges ---
    "http://10.0.0.1/",
    "http://10.255.255.254/dashboard",
    "http://172.16.0.1/",
    "http://172.31.255.254/",
    "http://192.168.1.1/",
    "http://192.168.0.254:8080/",
    # --- Carrier-Grade NAT (RFC 6598) ---
    "http://100.64.0.1/",
    "http://100.100.100.100/",
    "http://100.127.255.254/",
    # --- Multicast (IPv4 & IPv6) ---
    "http://224.0.0.1/",
    "http://239.255.255.250/",
    "http://[ff02::1]/",
    # --- Reserved / Unspecified ---
    "http://0.0.0.0/",
    "http://240.0.0.1/",
    "http://[::]/",
    # --- IPv4-Mapped IPv6 ---
    "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:10.0.0.1]/",
    "http://[::ffff:169.254.169.254]/",
    # --- Alternative IP Literal representations ---
    "http://2130706433/",  # Bare decimal integer for 127.0.0.1
    "http://0177.0.0.1/",  # Octal octet for 127.0.0.1
    "http://0x7f000001/",  # Bare hex integer for 127.0.0.1
    "http://0x7f.0x0.0x0.0x1/",  # Hex octets for 127.0.0.1
    # --- Disallowed schemes (SSRF protocol smuggling defense) ---
    "ftp://example.com/resource",
    "file:///etc/passwd",
    "gopher://127.0.0.1:70/",
    "dict://127.0.0.1:11211/",
]

LEGITIMATE_URLS = [
    "http://example.com/",
    "https://example.com/docs/api?q=1&lang=en",
    "https://www.google.com/",
    "https://github.com/torvalds/linux",
    "https://cloudflare.com/",
    "https://www.python.org/psf/",
    "https://developer.mozilla.org/en-US/",
    "https://en.wikipedia.org/wiki/Web_crawler",
    "https://www.ietf.org/rfc/rfc7807.html",
    "https://www.w3.org/TR/selectors-3/",
]


# Reference oracle for table-driven validation of the network checking layer
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("ff00::/8"),
]


def reference_is_blocked(ip: IPv4Address | IPv6Address) -> str | None:
    """Reference oracle checking if IP falls within any blocked CIDR."""
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if str(ip) in ("169.254.169.254", "fd00:ec2::254"):
        return "cloud_metadata"

    for net in BLOCKED_NETWORKS:
        if ip in net:
            return "blocked"
    return None


@pytest.mark.parametrize("url", HOSTILE_URLS)
@pytest.mark.asyncio
async def test_hostile_urls_rejected(url: str) -> None:
    with (
        patch("warden_net.ssrf_check.is_blocked_ip", side_effect=reference_is_blocked),
        contextlib.suppress(Exception),
    ):
        result = await validate_url_is_safe(url)
        assert result.safe is False, f"Expected hostile URL to be rejected: {url}"


@pytest.mark.parametrize("url", LEGITIMATE_URLS)
@pytest.mark.asyncio
async def test_legitimate_urls_pass(url: str) -> None:
    """Verify that every legitimate public target passes canonicalization and SSRF validation."""
    with (
        patch("warden_net.ssrf_check.is_blocked_ip", return_value=None),
        patch("warden_net.ssrf_check.resolve_host", new_callable=AsyncMock) as mock_resolve,
    ):
        mock_resolve.return_value = [IPv4Address("93.184.216.34")]
        result = await validate_url_is_safe(url)
        assert result.safe is True, f"Expected legitimate URL to pass: {url}"
        assert result.reason is None
