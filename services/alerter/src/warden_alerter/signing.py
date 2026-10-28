"""Versioned exact-byte signing, bounded freshness and constant-time verification."""

import hashlib
import hmac
import uuid


def sign(key: bytes, identity: str, timestamp: int, body: bytes) -> str:
    uuid.UUID(identity)
    if len(key) < 32 or len(body) > 65536 or timestamp < 0:
        raise ValueError("invalid_signing_input")
    return (
        "v1="
        + hmac.new(key, f"{timestamp}.{identity}.".encode() + body, hashlib.sha256).hexdigest()
    )


def verify(
    key: bytes,
    identity: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    now: int,
    tolerance_s: int = 300,
) -> bool:
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        return False
    if not 0 <= tolerance_s <= 600 or abs(now - int(timestamp)) > tolerance_s:
        return False
    try:
        expected = sign(key, identity, int(timestamp), body)
        return len(signature) == 67 and hmac.compare_digest(expected, signature)
    except (ValueError, TypeError):
        return False
