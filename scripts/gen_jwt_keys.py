#!/usr/bin/env python3
"""Generate an Ed25519 asymmetric keypair for WARDEN JWT signing and verification.

WHY ASYMMETRIC SIGNING (Ed25519 / EdDSA)?
=========================================
In a distributed architecture like WARDEN:
1. The trusted API service holds the PRIVATE key to sign access tokens on user login.
2. Any downstream service (such as the Day-4 untrusted fetcher or Day-19 Django analytics service)
   only needs the PUBLIC key to verify tokens.
3. Because the public key cannot be used to forge tokens, a compromise of an untrusted worker
   in the UNTRUSTED zone never compromises user token signing. With symmetric HMAC (HS256), every
   service verifying tokens must hold the secret key, meaning a compromise of any worker allows
   forging tokens for any user on the platform.

This script generates:
- keys/jwt_ed25519_private.pem: Private key in PKCS#8 format (No encryption for service load).
- keys/jwt_ed25519_public.pem: Public key in SubjectPublicKeyInfo format.
"""

import contextlib
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def generate_keys(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    priv_path = output_dir / "jwt_ed25519_private.pem"
    pub_path = output_dir / "jwt_ed25519_public.pem"

    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)

    # Secure permissions for private key
    with contextlib.suppress(OSError):
        priv_path.chmod(0o600)

    return priv_path, pub_path


def main() -> None:
    target_dir = Path("keys")
    if len(sys.argv) > 1:
        target_dir = Path(sys.argv[1])

    priv, pub = generate_keys(target_dir)
    print(f"Generated Ed25519 private key: {priv}")
    print(f"Generated Ed25519 public key:  {pub}")


if __name__ == "__main__":
    main()
