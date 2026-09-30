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

# SettingsConfigDict is the modern pydantic-settings v2 way to configure the
# Settings class behavior (replaces the old inner `class Config` pattern).
from pydantic import SecretStr
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
    """

    # SettingsConfigDict replaces the old inner `class Config`.
    # env_prefix: all env vars must start with WARDEN_
    # env_file: also read from .env file if present (useful for local dev)
    # env_file_encoding: standard utf-8
    model_config = SettingsConfigDict(
        env_prefix="WARDEN_",
        env_file=".env",
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
