"""Alembic environment configuration for warden_db.

WHAT IS THIS FILE?
==================
This file is the "brain" of Alembic. When you run any alembic command
(upgrade, downgrade, revision, etc.), Alembic first runs THIS file to set up:
  1. Which database to connect to
  2. Which tables it should "know about" (via our ORM models)
  3. Whether to run in "online" mode (actually connect to DB) or
     "offline" mode (just print the SQL without running it)

HOW AUTOGENERATE WORKS:
=======================
When you run `alembic revision --autogenerate -m "some change"`, Alembic:
  1. Looks at what tables exist RIGHT NOW in the real database
  2. Looks at what tables you've defined in your Python models (our ORM)
  3. Computes the DIFFERENCE (drift)
  4. Writes a migration script that makes the DB match the models

For step 2 to work, Alembic needs to import your models. That's why we
import warden_db.models below - it registers all table definitions onto
Base.metadata which Alembic then inspects.

WHY NullPool?
=============
NullPool means: "don't keep any database connections open after use".
During a migration run, we connect, apply changes, disconnect, done.
Using the normal connection pool during migrations would leave connections
hanging after the process exits, wasting resources and potentially causing
"too many connections" errors if migrations run frequently (e.g. in CI).

WHY READ FROM ENVIRONMENT VARIABLE?
====================================
Hard-coding the database URL here would mean:
  - It gets committed to git (a security risk)
  - It only works on one machine
Reading from WARDEN_DATABASE_URL means each environment (dev, staging,
production) just sets its own environment variable - same code everywhere.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Import our models so Alembic can "see" our table definitions.
#
# This import MUST come after the standard library / third-party imports
# (hence the E402 suppression - we need config set up first so sys.path
# includes the warden_db src directory via alembic.ini's prepend_sys_path).
#
# alembic's sys.path setup from alembic.ini to find the warden_db package).
# Base.metadata as a side effect; it IS used, just not referenced directly.
# ---------------------------------------------------------------------------
import warden_db.models  # noqa: F401
from warden_db.base import Base

# ---------------------------------------------------------------------------
# Step 1: Read the Alembic config (from alembic.ini)
# ---------------------------------------------------------------------------
config = context.config

# Set up Python's standard logging using the [loggers]/[handlers] sections
# in alembic.ini. This makes Alembic print progress during migrations.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata tells Alembic what the schema SHOULD look like (our models).
# It compares this against what's actually in the database to find drift.
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Step 2: Inject the real database URL from the environment.
#
# We MUST have WARDEN_DATABASE_URL set. If it's missing we fail fast with a
# clear error message rather than a confusing SQLAlchemy connection error.
# ---------------------------------------------------------------------------
_db_url = os.environ.get("WARDEN_DATABASE_URL")
if not _db_url:
    raise RuntimeError(
        "WARDEN_DATABASE_URL environment variable is not set.\n"
        "For local development, add this line to your .env file:\n"
        "  WARDEN_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost:5433/<db>\n"
        "For Docker Compose, this is set automatically from POSTGRES_* variables."
    )

# Override the placeholder URL from alembic.ini with the real one.
config.set_main_option("sqlalchemy.url", _db_url)


# ---------------------------------------------------------------------------
# OFFLINE MODE - generates SQL without connecting to the database
# ---------------------------------------------------------------------------
# Run with: alembic upgrade head --sql
# Useful for reviewing what SQL will be executed before you run it.
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL, don't execute it)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # compare_type=True means Alembic detects when a column's TYPE changes
        # (e.g. Integer -> BigInteger). Without this, only structural changes
        # (new columns, dropped columns) are detected, not type changes.
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# ONLINE MODE - actually connects to the database and runs migrations
# ---------------------------------------------------------------------------
# This is the normal mode when you run `alembic upgrade head`.


def do_run_migrations(connection: Connection) -> None:
    """Configure the migration context and execute migrations."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # detect column type changes during autogenerate
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations using it.

    WHY async_engine_from_config + NullPool?
    We build a brand-new engine specifically for migrations (not the app's
    shared pool). NullPool ensures no connections linger after the migration
    process exits - clean and deterministic lifecycle.
    """
    connectable = async_engine_from_config(
        # get_section() reads the [alembic] section from alembic.ini as a dict
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # NullPool: don't pool connections - create one, use it, close it.
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # run_sync() bridges async SQLAlchemy with the synchronous Alembic API.
        # Alembic's internal migration runner is synchronous, so we wrap it.
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online mode. Runs the async migration coroutine."""
    # asyncio.run() starts a new event loop, runs the coroutine, then shuts
    # the loop down. This is the standard way to run async code from a
    # synchronous context (Alembic calls this function synchronously).
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# ENTRY POINT - Alembic calls this file as a script; this block runs last.
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
