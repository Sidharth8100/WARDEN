"""Tests for student-implemented SSRF classification functions in warden_core.ssrf.

All tests are tagged @pytest.mark.student_todo and are excluded from the default test run.
Run them with: make check-todos (or uv run pytest -q -m student_todo).
"""

from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from warden_core.ssrf import is_blocked_ip, parse_ip_literal

# -----------------------------------------------------------------------------
# Reference CIDRs for blocked IP verification
# -----------------------------------------------------------------------------

BLOCKED_IPV4_NETWORKS = [
    ip_network("0.0.0.0/8"),  # "This network" (RFC 1122)
    ip_network("10.0.0.0/8"),  # RFC 1918 private
    ip_network("100.64.0.0/10"),  # Carrier-grade NAT (RFC 6598)
    ip_network("127.0.0.0/8"),  # Loopback (RFC 1122)
    ip_network("169.254.0.0/16"),  # Link-local (RFC 3927)
    ip_network("172.16.0.0/12"),  # RFC 1918 private
    ip_network("192.0.0.0/24"),  # IETF Protocol Assignments (RFC 6890)
    ip_network("192.0.2.0/24"),  # Documentation TEST-NET-1 (RFC 5737)
    ip_network("192.168.0.0/16"),  # RFC 1918 private
    ip_network("198.18.0.0/15"),  # Benchmarking (RFC 2544)
    ip_network("198.51.100.0/24"),  # Documentation TEST-NET-2 (RFC 5737)
    ip_network("203.0.113.0/24"),  # Documentation TEST-NET-3 (RFC 5737)
    ip_network("224.0.0.0/4"),  # Multicast (RFC 5771)
    ip_network("240.0.0.0/4"),  # Reserved (RFC 1112)
    ip_network("255.255.255.255/32"),  # Limited broadcast
]


def _reference_oracle_ipv4_blocked(addr: IPv4Address) -> bool:
    """Reference oracle checking if an IPv4 address falls within any blocked network."""
    return any(addr in net for net in BLOCKED_IPV4_NETWORKS)


# -----------------------------------------------------------------------------
# Tests for parse_ip_literal
# -----------------------------------------------------------------------------


@pytest.mark.student_todo
def test_parse_ip_literal_dotted_decimal():
    """Verify standard dotted-decimal IPv4 literals are parsed correctly."""
    assert parse_ip_literal("127.0.0.1") == IPv4Address("127.0.0.1")
    assert parse_ip_literal("10.0.0.1") == IPv4Address("10.0.0.1")
    assert parse_ip_literal("192.168.1.254") == IPv4Address("192.168.1.254")


@pytest.mark.student_todo
def test_parse_ip_literal_bare_decimal_integer():
    """Verify bare decimal integer IPv4 literals (e.g. 2130706433 for 127.0.0.1)."""
    # 127.0.0.1 in decimal integer is 2130706433
    assert parse_ip_literal("2130706433") == IPv4Address("127.0.0.1")
    # 10.0.0.1 in decimal integer is 167772161
    assert parse_ip_literal("167772161") == IPv4Address("10.0.0.1")


@pytest.mark.student_todo
def test_parse_ip_literal_octal_and_hex_octets():
    """Verify octal octets (0177.0.0.1) and hex octets (0x7f.0x0.0x0.0x1)."""
    assert parse_ip_literal("0177.0.0.1") == IPv4Address("127.0.0.1")
    assert parse_ip_literal("0x7f.0x0.0x0.0x1") == IPv4Address("127.0.0.1")
    assert parse_ip_literal("0x7f000001") == IPv4Address("127.0.0.1")


@pytest.mark.student_todo
def test_parse_ip_literal_ipv6_forms():
    """Verify standard and mapped IPv6 literals."""
    assert parse_ip_literal("::1") == IPv6Address("::1")
    assert parse_ip_literal("fe80::1") == IPv6Address("fe80::1")
    assert parse_ip_literal("::ffff:127.0.0.1") == IPv6Address("::ffff:127.0.0.1")


@pytest.mark.student_todo
def test_parse_ip_literal_regular_hostnames_return_none():
    """Verify non-literal domain names return None."""
    assert parse_ip_literal("example.com") is None
    assert parse_ip_literal("subdomain.internal.corp") is None
    assert parse_ip_literal("localhost") is None
    assert parse_ip_literal("google.com") is None


# -----------------------------------------------------------------------------
# Tests for is_blocked_ip
# -----------------------------------------------------------------------------


@pytest.mark.student_todo
@pytest.mark.parametrize(
    ("ip_str", "expected_reason"),
    [
        # Loopback
        ("127.0.0.1", "loopback"),
        ("127.0.0.2", "loopback"),
        ("127.255.255.254", "loopback"),
        ("::1", "loopback"),
        # Cloud metadata
        ("169.254.169.254", "cloud_metadata"),
        ("fd00:ec2::254", "cloud_metadata"),
        # Link-local (non-metadata)
        ("169.254.1.1", "link_local"),
        ("169.254.10.20", "link_local"),
        ("fe80::1", "link_local"),
        # RFC1918 Private
        ("10.0.0.1", "private"),
        ("172.16.0.1", "private"),
        ("192.168.1.1", "private"),
        # Carrier-grade NAT (CGNAT, 100.64.0.0/10)
        ("100.64.0.1", "cgnat"),
        ("100.100.100.100", "cgnat"),
        ("100.127.255.254", "cgnat"),
        # Multicast
        ("224.0.0.1", "multicast"),
        ("239.255.255.250", "multicast"),
        ("ff02::1", "multicast"),
        # Reserved / Unspecified
        ("0.0.0.0", "reserved"),  # noqa: S104
        ("240.0.0.1", "reserved"),
        ("::", "unspecified"),
        # IPv4-mapped IPv6
        ("::ffff:127.0.0.1", "loopback"),
        ("::ffff:10.0.0.1", "private"),
        ("::ffff:169.254.169.254", "cloud_metadata"),
    ],
)
def test_is_blocked_ip_table_driven(ip_str: str, expected_reason: str):
    """Table-driven test checking blocked classification for critical IPs."""
    parsed = ip_address(ip_str)
    reason = is_blocked_ip(parsed)
    assert reason is not None, f"Expected {ip_str} to be blocked"
    if expected_reason in ("reserved", "unspecified"):
        assert reason in ("reserved", "unspecified")
    else:
        assert reason == expected_reason


@pytest.mark.student_todo
@pytest.mark.parametrize(
    "ip_str",
    [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",  # example.com
        "151.101.1.140",
        "2606:4700:4700::1111",
    ],
)
def test_is_blocked_ip_legitimate_public_ips_allowed(ip_str: str):
    """Verify legitimate public routable IPs return None (fetchable)."""
    parsed = ip_address(ip_str)
    assert is_blocked_ip(parsed) is None


@pytest.mark.student_todo
@settings(max_examples=1000, suppress_health_check=[HealthCheck.too_slow])
@given(
    int_val=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_is_blocked_ip_hypothesis_oracle_agreement(int_val: int):
    """Property test asserting is_blocked_ip agrees with the reference oracle across 1000+ IPv4s."""
    addr = IPv4Address(int_val)
    expected_blocked = _reference_oracle_ipv4_blocked(addr)
    actual_reason = is_blocked_ip(addr)
    is_actual_blocked = actual_reason is not None

    assert is_actual_blocked == expected_blocked, (
        f"Mismatch for IPv4 {addr}: oracle says blocked={expected_blocked}, "
        f"actual returned reason={actual_reason}"
    )
