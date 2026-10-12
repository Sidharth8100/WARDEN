"""Password hashing, strength validation, and constant-time dummy verification."""

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from email_validator import EmailNotValidError, validate_email
from zxcvbn import zxcvbn  # type: ignore[import-untyped]

from warden_api.errors import APIError

# Default production hasher using argon2-cffi's current recommended parameters:
# time_cost=3, memory_cost=65536 (64 MiB), parallelism=4, hash_len=32, type=Argon2id.
# SECURITY RULE: Never hand-tune these down in production code. Tests that require
# high-speed hashing must inject a fast_hasher fixture, keeping application defaults untouched.
_hasher = PasswordHasher()


def hash_password(password: str, hasher: PasswordHasher | None = None) -> str:
    """Hash a plaintext password using Argon2id."""
    active_hasher = hasher or _hasher
    return active_hasher.hash(password)


def verify_password(password: str, hash_str: str, hasher: PasswordHasher | None = None) -> bool:
    """Verify a password against an Argon2id hash string."""
    active_hasher = hasher or _hasher
    try:
        return active_hasher.verify(hash_str, password)
    except (VerifyMismatchError, VerificationError):
        return False


def verify_dummy_password(
    password: str, dummy_hash: str, hasher: PasswordHasher | None = None
) -> bool:
    """Perform a dummy verification against a precomputed hash.

    This ensures failed login attempts for nonexistent user emails execute the
    exact same CPU-intensive Argon2id verification routine as existing users,
    preventing user enumeration via response timing discrepancies.
    """
    return verify_password(password, dummy_hash, hasher=hasher)


def validate_email_address(email: str) -> str:
    """Validate and normalize an email address."""
    if len(email) > 254:
        raise APIError(
            status_code=422,
            title="Validation Error",
            detail="Email address exceeds maximum length of 254 characters",
        )
    try:
        valid = validate_email(email, check_deliverability=False)
        return str(valid.normalized)
    except EmailNotValidError as exc:
        raise APIError(
            status_code=422,
            title="Validation Error",
            detail=f"Invalid email address: {exc}",
        ) from exc


def validate_password_strength(password: str) -> None:
    """Validate password length and cryptographic strength using zxcvbn.

    DoS VECTOR NOTE:
    The 128-character upper limit is a critical security barrier. Argon2id is
    computationally and memory-intensive by design. Allowing unbounded password
    lengths (e.g. multi-megabyte payloads) enables an unauthenticated attacker
    to consume 100% of the API's CPU/RAM via concurrent registration/login requests.
    """
    if len(password) < 12:
        raise APIError(
            status_code=422,
            title="Validation Error",
            detail="Password must be at least 12 characters long",
        )
    if len(password) > 128:
        raise APIError(
            status_code=422,
            title="Validation Error",
            detail="Password cannot exceed 128 characters (DoS prevention limit)",
        )

    # Score strength with zxcvbn
    analysis = zxcvbn(password)
    score = analysis.get("score", 0)

    if score < 2:
        feedback = analysis.get("feedback", {})
        suggestions = feedback.get("suggestions", [])
        warning = feedback.get("warning")
        msg_parts: list[str] = []
        if warning:
            msg_parts.append(warning)
        if suggestions:
            msg_parts.extend(suggestions)
        detail = (
            f"Password is too weak (score {score}/4). "
            f"Suggestions: {'; '.join(msg_parts) if msg_parts else 'Use a less predictable phrase'}"
        )
        raise APIError(
            status_code=422,
            title="Weak Password",
            detail=detail,
        )
