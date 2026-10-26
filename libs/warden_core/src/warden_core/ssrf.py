"""SSRF protection primitives for IP literal parsing and address classification.

Strictly standard-library only (ipaddress, socket). No network I/O.
"""

from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

# High-priority cloud metadata service endpoints (AWS, GCP, Azure, OpenStack IMDS)
CLOUD_METADATA_IPV4 = IPv4Address("169.254.169.254")
CLOUD_METADATA_IPV6 = IPv6Address("fd00:ec2::254")

# Carrier-Grade NAT (CGNAT) / Shared Address Space (RFC 6598: 100.64.0.0/10)
# Standard Python ipaddress does not treat 100.64.0.0/10 as private, so we check explicitly.
CGNAT_NETWORK = ip_network("100.64.0.0/10")

# "This network" (RFC 1122: 0.0.0.0/8)
THIS_NETWORK = ip_network("0.0.0.0/8")

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


def _parse_octet(part: str) -> int | None:
    """Parse a single IPv4 octet supporting decimal, octal (0177), and hex (0x7f).

    Returns an integer in [0, 255] on success, or None if invalid.
    """
    clean_part = part.strip()
    if not clean_part:
        return None

    try:
        # Check for hexadecimal notation (0x7f, 0X7F)
        if clean_part.lower().startswith("0x"):
            val = int(clean_part, 16)
        # Check for octal notation: POSIX leading '0' (e.g. "0177") or Python '0o' prefix
        elif clean_part.lower().startswith("0o") or (
            clean_part.startswith("0") and len(clean_part) > 1
        ):
            val = int(clean_part, 8)
        # Standard base-10 decimal notation (e.g., "127")
        else:
            val = int(clean_part, 10)

        # Ensure the octet fits within 8 bits (0-255)
        if 0 <= val <= 255:
            return val
        return None
    except ValueError:
        # Not a valid numeric octet
        return None


def parse_ip_literal(host: str) -> IPv4Address | IPv6Address | None:
    """If `host` is a LITERAL IP address written in ANY form an attacker might
    use to confuse a naive parser, return the parsed address. Otherwise (a genuine
    hostname like "example.com") return None.

    Handles:
    - Standard dotted-decimal IPv4 (127.0.0.1)
    - Bare decimal 32-bit integer (2130706433 -> 127.0.0.1)
    - Bare hexadecimal 32-bit integer (0x7f000001 -> 127.0.0.1)
    - Mixed octal octets (0177.0.0.1 -> 127.0.0.1)
    - Mixed hex octets (0x7f.0x0.0x0.0x1 -> 127.0.0.1)
    - Standard and mapped IPv6 literals (::1, [::1], fe80::1, ::ffff:127.0.0.1)
    """
    if not host or not isinstance(host, str):
        return None

    # Step 1: Strip surrounding whitespace and URI IPv6 bracket delimiters (e.g., "[::1]" -> "::1")
    cleaned = host.strip().strip("[]")

    # Step 2: Check for IPv6 literals (IPv6 addresses always contain colons)
    if ":" in cleaned:
        try:
            addr = ip_address(cleaned)
            if isinstance(addr, IPv6Address):
                return addr
        except ValueError:
            # Contains colons but is not a valid IPv6 literal
            return None

    # Step 3: Check for bare integer representations of 32-bit IPv4 addresses (no dots)
    # E.g., decimal "2130706433" or hex "0x7f000001"
    if "." not in cleaned:
        try:
            if cleaned.lower().startswith("0x"):
                # Parse as hexadecimal
                val = int(cleaned, 16)
            elif cleaned.isdigit():
                # Parse as plain decimal integer
                val = int(cleaned, 10)
            else:
                return None

            # A valid 32-bit IPv4 integer must be between 0 and 2^32 - 1
            if 0 <= val <= 0xFFFFFFFF:
                return IPv4Address(val)
        except ValueError:
            pass
        return None

    # Step 4: Check for 4-part dotted IPv4 notation (e.g. 127.0.0.1, 0177.0.0.1, 0x7f.0.0.1)
    parts = cleaned.split(".")
    if len(parts) == 4:
        # Parse each octet with support for decimal, octal, and hex
        o0 = _parse_octet(parts[0])
        o1 = _parse_octet(parts[1])
        o2 = _parse_octet(parts[2])
        o3 = _parse_octet(parts[3])

        # If all 4 octets parsed successfully within [0, 255]
        if o0 is not None and o1 is not None and o2 is not None and o3 is not None:
            # Combine the 4 octets into a single 32-bit unsigned integer using bit shifts
            val = (o0 << 24) | (o1 << 16) | (o2 << 8) | o3
            return IPv4Address(val)

    # Step 5: If none of the IP literal patterns match, treat as a regular hostname
    return None


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
    """Return a short machine-readable reason string (e.g. "loopback", "private",
    "link_local", "cloud_metadata", "cgnat", "multicast", "reserved", "unspecified")
    if `ip` must NEVER be fetched by WARDEN. Return None if it is safe and fetchable.
    """
    # Step 1: For IPv4-mapped IPv6 addresses (::ffff:x.x.x.x), unwrap the embedded IPv4
    # address and evaluate it recursively against IPv4 rules.
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        return is_blocked_ip(ip.ipv4_mapped)

    # Step 2: Specific high-priority cloud metadata service addresses (IMDS).
    # AWS/GCP/Azure/OpenStack metadata endpoints (169.254.169.254, fd00:ec2::254).
    # Must be explicitly identified for security audit logging.
    if ip in (CLOUD_METADATA_IPV4, CLOUD_METADATA_IPV6):
        return "cloud_metadata"

    # Step 3: Loopback addresses (127.0.0.0/8, ::1)
    if ip.is_loopback:
        return "loopback"

    # Step 4: Link-local addresses (169.254.0.0/16, fe80::/10)
    if ip.is_link_local:
        return "link_local"

    # Step 5: Carrier-Grade NAT (CGNAT) / Shared Address Space (RFC 6598: 100.64.0.0/10)
    # Python's ip.is_private returns False for CGNAT, so we test subnet membership explicitly.
    if isinstance(ip, IPv4Address) and ip in CGNAT_NETWORK:
        return "cgnat"

    # Step 6: Multicast addresses (224.0.0.0/4, ff00::/8)
    if ip.is_multicast:
        return "multicast"

    # Step 7: Unspecified addresses (0.0.0.0, ::)
    if ip.is_unspecified:
        return "unspecified"

    # Step 8: Reserved / limited broadcast addresses (240.0.0.0/4, 0.0.0.0/8, 255.255.255.255)
    if ip.is_reserved or (isinstance(ip, IPv4Address) and ip in THIS_NETWORK):
        return "reserved"

    # Step 9: RFC 1918 private IPv4 networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    # and Unique Local IPv6 Addresses (ULA: fc00::/7)
    if ip.is_private:
        return "private"

    # Step 10: Legitimate, publicly routable IP address
    return None
