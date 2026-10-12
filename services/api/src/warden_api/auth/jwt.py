"""Asymmetric JWT issuing and verification using Ed25519 (EdDSA)."""

import secrets
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from warden_api.errors import APIError
from warden_api.settings import Settings


@lru_cache
def get_jwt_private_key(path_str: str) -> Ed25519PrivateKey:
    """Load and cache the Ed25519 private key from PEM file."""
    path = Path(path_str)
    if not path.is_file():
        raise RuntimeError(
            f"JWT private key not found at '{path_str}'. Run scripts/gen_jwt_keys.py first."
        )
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"Expected Ed25519PrivateKey, got {type(key)}")
    return key


@lru_cache
def get_jwt_public_key(path_str: str) -> Ed25519PublicKey:
    """Load and cache the Ed25519 public key from PEM file."""
    path = Path(path_str)
    if not path.is_file():
        raise RuntimeError(
            f"JWT public key not found at '{path_str}'. Run scripts/gen_jwt_keys.py first."
        )
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError(f"Expected Ed25519PublicKey, got {type(key)}")
    return key


def create_access_token(
    user_id: uuid.UUID,
    role: str,
    settings: Settings,
    *,
    custom_ttl: int | None = None,
) -> str:
    """Create a signed Ed25519 access token.

    Claims:
    - sub: string representation of user UUID
    - role: user role ('user', 'admin')
    - iat: timestamp issued at (UTC)
    - exp: expiration timestamp (UTC)
    - jti: cryptographically secure random token ID (secrets.token_urlsafe)
    Header:
    - kid: Key ID identifying this key for rotation
    """
    private_key = get_jwt_private_key(settings.jwt_private_key_path)
    now = datetime.now(UTC)
    ttl = custom_ttl if custom_ttl is not None else settings.access_token_ttl_seconds
    exp = int(now.timestamp()) + ttl

    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": exp,
        "jti": secrets.token_urlsafe(32),
    }

    headers = {"kid": settings.jwt_kid}
    return jwt.encode(payload, private_key, algorithm="EdDSA", headers=headers)


def decode_access_token(
    token: str,
    settings: Settings,
) -> dict[str, Any]:
    """Decode and verify an incoming access token.

    SECURITY RULE (Hard Constraint 3):
    Explicitly enforces algorithms=["EdDSA"]. The verification MUST NEVER
    honor or trust the token's incoming 'alg' header claim. This strictly prevents
    algorithm confusion attacks (e.g. alg='none' or treating public key bytes as HMAC secret).
    """
    public_key = get_jwt_public_key(settings.jwt_public_key_path)
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=["EdDSA"],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "require": ["sub", "exp", "jti", "role"],
            },
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Access token has expired",
            headers={
                "WWW-Authenticate": ('Bearer error="invalid_token", error_description="expired"')
            },
        ) from exc
    except jwt.PyJWTError as exc:
        raise APIError(
            status_code=401,
            title="Unauthorized",
            detail="Invalid or malformed access token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
