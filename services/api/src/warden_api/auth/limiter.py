"""Temporary in-memory rate limiting for login attempts.

DISTRIBUTED-SAFETY WARNING:
===========================
This in-memory rate limiter stores state in process memory (a Python dictionary).
It is NOT distributed-safe and does NOT share state across multiple API replica
containers. On Day 8, this will be replaced with a distributed Redis Generic Cell
Rate Algorithm (GCRA) limiter built by hand.
"""

import time
from collections import defaultdict

from warden_api.errors import APIError

# Map key -> list of failure timestamps: float
_failure_records: dict[str, list[float]] = defaultdict(list)

# Max failures allowed in window before lockout
MAX_FAILURES = 5
WINDOW_SECONDS = 300  # 5 minutes


def _clean_old_records(key: str, now: float) -> list[float]:
    """Remove entries older than the sliding window."""
    cutoff = now - WINDOW_SECONDS
    active = [t for t in _failure_records[key] if t > cutoff]
    _failure_records[key] = active
    return active


def check_rate_limit(ip: str | None, email: str | None) -> None:
    """Check if the IP or email is currently locked out due to too many failed attempts."""
    now = time.time()
    keys_to_check: list[str] = []
    if ip:
        keys_to_check.append(f"ip:{ip}")
    if email:
        keys_to_check.append(f"email:{email.lower().strip()}")

    for k in keys_to_check:
        active = _clean_old_records(k, now)
        if len(active) >= MAX_FAILURES:
            retry_after = int(WINDOW_SECONDS - (now - active[0]))
            raise APIError(
                status_code=429,
                title="Too Many Requests",
                detail=(
                    "Too many failed login attempts. "
                    f"Please try again in {max(1, retry_after)} seconds."
                ),
                headers={"Retry-After": str(max(1, retry_after))},
            )


def record_login_failure(ip: str | None, email: str | None) -> None:
    """Record a login failure timestamp for IP and account."""
    now = time.time()
    if ip:
        _failure_records[f"ip:{ip}"].append(now)
    if email:
        _failure_records[f"email:{email.lower().strip()}"].append(now)


def reset_login_limit(ip: str | None, email: str | None) -> None:
    """Reset rate limit failure counters upon successful authentication."""
    if ip:
        _failure_records.pop(f"ip:{ip}", None)
    if email:
        _failure_records.pop(f"email:{email.lower().strip()}", None)
