"""warden_net: safe URL parsing, canonicalization, and SSRF-safe HTTP fetching."""

from warden_net.canonicalize import CanonicalUrl, canonicalize_url
from warden_net.ssrf_check import (
    SafeFetchResult,
    SSRFCheckResult,
    resolve_host,
    safe_get,
    validate_url_is_safe,
)

__all__ = [
    "CanonicalUrl",
    "SSRFCheckResult",
    "SafeFetchResult",
    "canonicalize_url",
    "resolve_host",
    "safe_get",
    "validate_url_is_safe",
]
