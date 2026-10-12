"""SSRF protection primitives for IP literal parsing and address classification.

Strictly standard-library only (ipaddress, socket). No network I/O.
"""

from ipaddress import IPv4Address, IPv6Address

# -----------------------------------------------------------------------------
# Hint Ladder for parse_ip_literal:
# Level 1 (Conceptual):
#   An IP address literal might appear as standard dotted-decimal (127.0.0.1),
#   standard IPv6 (::1), or obscured variants like a 32-bit decimal integer
#   (2130706433), hex values (0x7f000001 or 0x7f.0x0.0x0.0x1), or octal.
#   Determine whether the host string represents any numeric IP literal without
#   contacting DNS. If it is a domain name (e.g., "example.com"), return None.
#
# Level 2 (Pseudocode):
#   1. Strip brackets if IPv6 literal like "[::1]".
#   2. Try parsing with ipaddress.ip_address(host). If valid, return it.
#   3. Try parsing host as a single integer (decimal, hex with 0x) -> IPv4Address(int_val).
#   4. If host has dots, check if parts can be parsed as integers (supporting octal/hex)
#      and combined into a 32-bit IPv4 address.
#   5. If everything fails or raises ValueError, return None.
#
# Level 3 (Stdlib Reference):
#   - ipaddress.ip_address(str)
#   - ipaddress.IPv4Address(int)
#   - int(string, 0) (auto-detects base 16/10/8 if prefixes like 0x/0o exist)
#   - socket.inet_aton (note platform differences on leading zeros)
# -----------------------------------------------------------------------------


def parse_ip_literal(host: str) -> IPv4Address | IPv6Address | None:
    """[YOU IMPLEMENT] If `host` is a LITERAL IP address written in ANY
    form an attacker might use to confuse a naive parser, return the
    parsed address. Otherwise (a genuine hostname like "example.com")
    return None. Must handle at minimum: standard dotted-decimal
    (127.0.0.1); a bare decimal integer (2130706433); octal octets
    (0177.0.0.1); hex octets (0x7f.0x0.0x0.0x1) and a bare hex integer
    (0x7f000001); IPv6 forms including the IPv4-mapped form
    (::ffff:127.0.0.1) and bare loopback (::1). Do NOT perform any DNS
    resolution here — literal strings only.
    Hint L1: Python's ipaddress module already parses standard dotted-decimal
    and IPv6 — the interesting work is normalizing the NON-standard forms
    (decimal/octal/hex) into something ipaddress.ip_address() accepts, or
    computing the integer value yourself and using ipaddress.IPv4Address(int).
    Hint L2: split on '.', try each octet as int(octet, 0) (what does base=0
    do in Python's int()?) then check if the whole thing collapses to a
    single integer (a "decimal IP") by trying int(host, 0) on the whole
    string first before splitting.
    Hint L3: int(x, 0) auto-detects 0x/0o/0 prefixes. ipaddress.IPv4Address
    accepts either a dotted string OR a plain int (the raw 32-bit value).
    Watch for a bare leading '0' meaning octal in some parsers but not in
    Python's int(x, 0) — decide and document your behavior, then test it.
    """
    raise NotImplementedError("see docstring")


# -----------------------------------------------------------------------------
# Hint Ladder for is_blocked_ip:
# Level 1 (Conceptual):
#   Inspect the IP address against all dangerous network ranges: loopback,
#   private (RFC1918), carrier-grade NAT (RFC6598), link-local, multicast,
#   cloud metadata (169.254.169.254 and fd00:ec2::254), and reserved ranges.
#   Return a specific descriptive reason string if blocked, or None if safe.
#
# Level 2 (Pseudocode):
#   1. If IPv6 and address is IPv4-mapped (ip.ipv4_mapped is not None),
#      recurse: return is_blocked_ip(ip.ipv4_mapped).
#   2. Check specific high-priority addresses first:
#      - If ip == IPv4Address("169.254.169.254"): return "cloud_metadata"
#      - If ip == IPv6Address("fd00:ec2::254"): return "cloud_metadata"
#   3. Check ip properties:
#      - if ip.is_loopback: return "loopback"
#      - if ip.is_link_local: return "link_local"
#      - if ip.is_multicast: return "multicast"
#      - if ip.is_reserved: return "reserved"
#      - if ip.is_unspecified: return "unspecified"
#   4. Check CIDR networks not fully covered or needing specific names:
#      - CGNAT: 100.64.0.0/10 -> "cgnat"
#      - if ip.is_private: return "private"
#   5. Return None if not blocked.
#
# Level 3 (Stdlib Reference):
#   - IPv4Address / IPv6Address properties: .is_private, .is_loopback,
#     .is_link_local, .is_multicast, .is_reserved, .is_unspecified, .ipv4_mapped
#   - ipaddress.ip_network("100.64.0.0/10")
#   - `ip in network` membership testing
# -----------------------------------------------------------------------------


def is_blocked_ip(ip: IPv4Address | IPv6Address) -> str | None:
    """[YOU IMPLEMENT] Return a short machine-readable reason string
    (e.g. "loopback", "private", "link_local", "cloud_metadata",
    "cgnat", "multicast", "reserved") if `ip` must NEVER be fetched by
    WARDEN. Return None if it is fetchable. Must block at minimum:
    loopback; link-local (169.254.0.0/16 AND specifically call out
    169.254.169.254, the AWS/GCP/Azure metadata address, even though
    it's already inside that /16 — name it specifically in your reason
    string so it's auditable in logs); IPv6 link-local (fe80::/10) and
    the IPv6 metadata address (fd00:ec2::254); RFC1918 private ranges;
    carrier-grade NAT (100.64.0.0/10); multicast; reserved/unspecified;
    and an IPv4-mapped IPv6 address (::ffff:x.x.x.x) whose EMBEDDED
    IPv4 address is itself blocked (recurse into this same function).
    Hint L1: Python's ipaddress objects expose boolean properties for
    several of these already — read the ipaddress module docs for
    IPv4Address/IPv6Address before writing anything by hand.
    Hint L2: is_private, is_loopback, is_link_local, is_multicast,
    is_reserved, is_unspecified exist. VERIFY — don't assume — whether
    is_private on your installed Python version already covers
    100.64.0.0/10; if not, check containment in that network explicitly
    with ipaddress.ip_network("100.64.0.0/10").
    Hint L3: ipaddress.ip_address(x) in ipaddress.ip_network(cidr) does
    containment. For the IPv4-mapped case, IPv6Address has an
    `.ipv4_mapped` property returning the embedded IPv4Address or None.
    """
    raise NotImplementedError("see docstring")
