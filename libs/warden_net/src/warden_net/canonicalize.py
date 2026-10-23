"""URL parsing, normalization, canonicalization, and hashing.

This module is dependency-light (httpx, idna, tldextract, stdlib only).
"""

import hashlib
import posixpath
import urllib.parse
from dataclasses import dataclass
from ipaddress import IPv6Address, ip_address

import idna
import tldextract

_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


@dataclass(frozen=True)
class CanonicalUrl:
    """Represents a validated, normalized canonical URL."""

    url: str
    registrable_domain: str
    url_hash: bytes


def canonicalize_url(raw: str) -> CanonicalUrl:
    """Canonicalize a raw URL according to WARDEN specifications.

    Steps:
    1. Parse with urllib.parse.urlsplit.
    2. Reject anything but http and https (case-insensitive, normalize to lowercase).
    3. IDNA-encode the hostname via the `idna` package.
       WHY `idna` instead of Python's built-in `.encode('idna')`:
       The standard library codec implements the legacy IDNA2003 standard (RFC 3490).
       IDNA2003 has known defects and differences from IDNA2008 / UTS #46 regarding
       deviation characters (e.g. sharp s 'ß', Greek final sigma 'ς') and newer Unicode
       scripts. The `idna` package implements IDNA2008 (RFC 5891) with UTS #46 compatibility.
    4. Lowercase the hostname.
    5. Strip default ports (80 for http, 443 for https), retaining explicit non-default ports.
    6. Resolve '.' and '..' path segments via posixpath.normpath.
    7. Strip the URL fragment entirely.
    8. Sort query parameters for stable hashing and deduplication.
    9. Compute registrable_domain (eTLD+1) via tldextract (e.g. 'example.co.uk').
    10. Compute url_hash = SHA-256 of the canonical URL string as raw bytes (bytea).
    """
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("URL cannot be empty")

    parsed = urllib.parse.urlsplit(cleaned)

    # 1. Validate scheme
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        msg = f"Unsupported URL scheme '{parsed.scheme}': only http and https are allowed"
        raise ValueError(msg)

    # 2. Host validation and IDNA normalization
    raw_host = parsed.hostname
    if not raw_host:
        raise ValueError("URL must include a valid host")

    raw_host = raw_host.strip()
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL userinfo is not permitted")
    try:
        literal = ip_address(raw_host)
    except ValueError:
        literal = None

    # Handle IPv6 bracketed literals vs regular hostnames
    if isinstance(literal, IPv6Address):
        canonical_host = f"[{literal.compressed}]"
        host_for_tld = literal.compressed
    else:
        try:
            canonical_host = idna.encode(raw_host.lower()).decode("ascii").lower()
        except idna.IDNAError as exc:
            err = f"Invalid domain name or IDNA encoding failure for '{raw_host}': {exc}"
            raise ValueError(err) from exc
        host_for_tld = canonical_host

    # 3. Port normalization
    port = parsed.port
    include_port = False
    if port is not None and (
        (scheme == "http" and port != 80) or (scheme == "https" and port != 443)
    ):
        include_port = True

    netloc = f"{canonical_host}:{port}" if include_port else canonical_host

    # 4. Path normalization (resolving . and ..)
    raw_path = parsed.path or "/"
    had_trailing_slash = raw_path.endswith("/")
    norm_path = posixpath.normpath(raw_path)
    if not norm_path.startswith("/"):
        norm_path = f"/{norm_path}"
    if had_trailing_slash and not norm_path.endswith("/"):
        norm_path = f"{norm_path}/"

    # 5. Query parameter sorting
    if parsed.query:
        query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query_items.sort(key=lambda pair: (pair[0], pair[1]))
        canonical_query = urllib.parse.urlencode(query_items)
    else:
        canonical_query = ""

    # 6. Reconstruct canonical URL (fragment is intentionally stripped)
    canonical_str = urllib.parse.urlunsplit((scheme, netloc, norm_path, canonical_query, ""))

    # 7. Registrable domain via tldextract (eTLD+1)
    extracted = _EXTRACTOR(host_for_tld)
    reg_domain = extracted.top_domain_under_public_suffix or host_for_tld

    # 8. SHA-256 hash as raw bytes
    url_hash = hashlib.sha256(canonical_str.encode("utf-8")).digest()

    return CanonicalUrl(
        url=canonical_str,
        registrable_domain=reg_domain,
        url_hash=url_hash,
    )
