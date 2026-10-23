"""Application settings for the WARDEN API service.

WHAT IS pydantic-settings?
===========================
pydantic-settings is a library that reads configuration from environment
variables and validates them using Python type hints. It's like a strongly-typed
version of os.environ.get().

Why use it instead of os.environ.get() directly?
  - Type validation: if you put a non-integer where an int is expected, it
    raises a clear error at startup instead of a confusing crash later.
  - Secret handling: SecretStr values are never printed in logs or repr() -
    the string "**********" appears instead, preventing accidental secret leaks.
  - Documentation: the Settings class IS the documentation of every config
    variable the service needs.

HOW env_prefix WORKS:
=====================
With env_prefix="WARDEN_", the field `database_url` maps to the environment
variable `WARDEN_DATABASE_URL`. This namespacing prevents conflicts with other
apps' environment variables.

WHAT IS lru_cache?
==================
`get_settings()` is decorated with @lru_cache. This means:
  - The first time it's called, it reads all env vars and builds the Settings object.
  - Every subsequent call returns the SAME cached object (no re-reading env vars).
  - This is efficient AND it means the whole app uses exactly one Settings instance.
  - In tests, you can call get_settings.cache_clear() to reset the cache.

WHY NOT a module-level Settings() instance?
============================================
A module-level `settings = Settings()` would run at import time, before any
test fixtures have a chance to set environment variables. The lru_cache pattern
lets tests control settings by patching environment variables before the first
call to get_settings().
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration the WARDEN API service needs to run.

    Every field here corresponds to an environment variable prefixed with WARDEN_.
    Fields without defaults are REQUIRED - the app will refuse to start if missing.
    Fields with defaults are optional and fall back to sensible values.

    REQUIRED (must be in .env or the environment):
      WARDEN_DATABASE_URL  - PostgreSQL connection string
      WARDEN_REDIS_URL     - Redis connection string

    OPTIONAL (have defaults):
      WARDEN_OBJECTSTORE_URL   - SeaweedFS S3 endpoint (default: in-compose host)
      WARDEN_ALEMBIC_INI_PATH  - path to alembic.ini for /ready check
      WARDEN_LOG_LEVEL         - Python logging level string
      WARDEN_JSON_LOGS         - whether to output structured JSON logs
      WARDEN_ENV               - 'dev' or 'prod' (default: 'dev')
      WARDEN_COOKIE_SECURE     - cookie Secure attribute (defaults to env == 'prod')
      WARDEN_JWT_PRIVATE_KEY_PATH - path to Ed25519 private PEM
      WARDEN_JWT_PUBLIC_KEY_PATH  - path to Ed25519 public PEM
      WARDEN_JWT_KID           - key identifier header for JWT rotation
      WARDEN_ACCESS_TOKEN_TTL_SECONDS  - access token lifetime (default: 900s / 15m)
      WARDEN_REFRESH_TOKEN_TTL_SECONDS - refresh token lifetime (default: 30 days)
    """

    # SettingsConfigDict replaces the old inner `class Config`.
    # env_prefix: all env vars must start with WARDEN_
    # env_file: also read from .env file if present (useful for local dev)
    # env_file_encoding: standard utf-8
    model_config = SettingsConfigDict(
        env_prefix="WARDEN_",
        env_file=None,
        env_file_encoding="utf-8",
        # extra="ignore" means unknown WARDEN_* variables in .env are silently
        # ignored rather than raising a validation error. This is important
        # because .env also contains POSTGRES_* variables for Docker Compose.
        extra="ignore",
    )

    # SecretStr wraps the string so that:
    #   - repr(settings) shows "**********" not the actual password
    #   - str(settings.database_url) returns "**********"
    #   - settings.database_url.get_secret_value() returns the real string
    # This prevents accidental secret exposure in logs, error messages, etc.
    database_url: SecretStr  # WARDEN_DATABASE_URL - required, no default
    redis_url: SecretStr  # WARDEN_REDIS_URL - required, no default

    # http://objectstore:8333 uses the Docker Compose service name as the
    # hostname. Inside the compose network, containers reach each other by
    # their service name (not "localhost"). Outside compose (tests), this
    # will fail to connect, which is intentional - tests mock this out.
    objectstore_url: str = "http://objectstore:8333"

    # Path to alembic.ini INSIDE the running container (set in Dockerfile).
    # The /ready endpoint reads this to check if migrations are up to date.
    alembic_ini_path: str = "/app/db/alembic.ini"

    log_level: str = "INFO"
    json_logs: bool = True

    # Prompt 2 settings
    env: Literal["dev", "prod"] = "dev"
    cookie_secure: bool = True

    jwt_private_key_path: str = "keys/jwt_ed25519_private.pem"
    jwt_public_key_path: str = "keys/jwt_ed25519_public.pem"
    jwt_kid: str = "warden-key-1"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000

    # Precomputed Argon2id dummy hash used on failed login for nonexistent users
    # so that timing is shaped identically to an actual hash verification.
    dummy_password_hash: str = (
        "$argon2id$v=19$m=65536,t=3,p=4$9CNq2oL4dlqpkw48rrhl+w"  # noqa: S105
        "$25a/S4cvOKLw1p9bW0+JpZ2OLGRVRz2h6D3V8c5jYYA"
    )

    # Prompt 3 settings
    objectstore_access_key: SecretStr = SecretStr("any")
    objectstore_secret_key: SecretStr = SecretStr("any")
    objectstore_endpoint: str = "http://objectstore:8333"
    objectstore_bucket: str = "warden"

    ssrf_test_allowlist: tuple[str, ...] = ()

    selenium_hub_url: str = "http://selenium-hub:4444"
    fetch_default_timeout_s: float = 15.0
    fetch_max_bytes: int = 10_000_000
    keyframe_interval: int = 20
    ws_allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:18000",
        "http://127.0.0.1:18001",
        "http://localhost:18000",
        "http://localhost:18001",
    )

    @model_validator(mode="before")
    @classmethod
    def set_cookie_secure(cls, data: Any) -> Any:
        """Default cookie_secure to False in dev and True in prod unless explicitly overridden."""
        if isinstance(data, dict) and (
            "cookie_secure" not in data or data.get("cookie_secure") is None
        ):
            data["cookie_secure"] = data.get("env", "dev") == "prod"
        return data


@lru_cache  # Returns the same Settings instance on every call after the first
def get_settings() -> Settings:
    """Return the application settings singleton.

    The @lru_cache decorator memoizes this function so Settings() is only
    instantiated once per process. To reset in tests:
        get_settings.cache_clear()
    """
    return Settings()
    # The pydantic mypy plugin (configured in pyproject.toml) teaches mypy
    # that pydantic fills required fields from the environment, so no
    # arguments are needed here despite database_url/redis_url being required.
