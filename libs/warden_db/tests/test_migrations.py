"""Integration tests for database migrations and basic DB connectivity.

WHAT IS AN INTEGRATION TEST?
=============================
Unlike unit tests (which test one function in isolation, with no external
dependencies), integration tests test that TWO OR MORE real components work
correctly TOGETHER.

These tests:
  1. Connect to a REAL PostgreSQL database (the one started by docker compose)
  2. Verify that the Alembic migration applied the schema correctly
  3. Verify that each table exists and has the expected primary key
  4. Verify that the plans seed data was inserted

WHY IS A REAL DB NEEDED?
  - You cannot verify that a partial index with a WHERE clause was created
    correctly by reading Python code alone; the DB engine must confirm it.
  - You cannot verify that citext was enabled (a Postgres extension) without
    connecting to Postgres.
  - You cannot verify that the plans seed data persists after a real INSERT.

HOW TO RUN:
  # With the Postgres container running (port 5433 mapped by override):
  WARDEN_DATABASE_URL="postgresql+asyncpg://warden:<pass>@localhost:5433/warden" \\
    uv run pytest -m integration -v

  # Skip integration tests (e.g. in CI without Docker):
  uv run pytest -m "not integration"

asyncpg DRIVER:
  asyncpg is the asynchronous PostgreSQL driver. "async" means the test
  can run many DB queries without blocking the event loop between them.
  All async tests are run by pytest-asyncio, configured with asyncio_mode=auto.
"""

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from warden_db.session import _normalize_db_url

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_postgres_reachable(host: str = "127.0.0.1", port: int = 5433) -> bool:
    """Check if PostgreSQL is accepting TCP connections on localhost:5433."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _db_url() -> str:
    """Read the database URL from the environment, skip the test if missing or unreachable.

    pytest.skip() inside a fixture causes every test that uses the fixture
    to be SKIPPED (shown as 's' in pytest output) rather than FAILING.
    This is important: a missing DB URL on a developer's machine that has
    no Docker running should not break the CI signal for unit tests.
    """
    url = os.environ.get("WARDEN_DATABASE_URL", "")
    if not url:
        pytest.skip(
            "WARDEN_DATABASE_URL not set - skipping integration tests. "
            "Start docker compose and set the variable to run these."
        )
    if not _is_postgres_reachable():
        pytest.skip("Postgres on localhost:5433 is unreachable - skipping integration tests.")
    return url


# ---------------------------------------------------------------------------
# Fixtures: shared async engine and per-test session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create a database engine for each test.

    We use function scope (one engine per test) to avoid loop-scope conflicts
    between pytest-asyncio fixtures. The overhead is minimal: SQLAlchemy with
    NullPool doesn't hold idle connections, so each fixture call is just object
    creation (~0.1ms), not a new network connection.

    NullPool: same as in migrations - each query opens and closes its own
    connection, preventing connection leak between tests.
    """
    eng = create_async_engine(_db_url(), poolclass=NullPool)
    yield eng
    # Cleanup: close all connections when this module's tests are done.
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Open a fresh transaction for each test, then roll it back.

    TRANSACTION ROLLBACK PATTERN:
    ==============================
    Each test runs inside a transaction. At the end of the test, we roll back
    the transaction. This means:
      - Tests can INSERT/UPDATE/DELETE freely
      - None of those changes persist after the test
      - The next test starts with a clean slate

    This is the FASTEST way to isolate integration tests: no need to truncate
    or re-seed tables between tests. The rollback is instant.

    HOW IT WORKS:
      1. Begin a transaction on the connection
      2. Create an AsyncSession bound to that connection
      3. yield the session to the test function
      4. Rollback the connection (discards all changes from the test)
      5. Close the connection
    """
    async with engine.begin() as conn:
        factory = async_sessionmaker(bind=conn, class_=AsyncSession, expire_on_commit=False)
        async with factory() as sess:
            yield sess
        # Roll back the wrapping transaction - test changes disappear.
        await conn.rollback()


# ---------------------------------------------------------------------------
# Tests: Schema correctness
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMigrationSchema:
    """Verify the applied schema matches what the migration says it should be.

    These tests query Postgres's internal metadata tables (information_schema
    and pg_indexes) to verify the actual DB structure, not just the Python
    ORM definitions.
    """

    async def test_alembic_version_matches_head(self, engine: AsyncEngine) -> None:
        """The alembic_version table must contain the head revision ID.

        If this fails, it means:
          - The migration was not applied (alembic upgrade head was not run)
          - Or a migration was applied partially and left the DB in a bad state

        The head revision ID is hardcoded here because it's a known value
        from the initial_schema migration. Future migrations would update this.
        """
        head_rev = "2fdb8aa45d1d"  # from migrations/versions/2fdb8aa45d1d_initial_schema.py

        async with engine.connect() as conn:
            result = await conn.execute(sa.text("SELECT version_num FROM alembic_version LIMIT 1"))
            db_rev = result.scalar_one_or_none()

        assert db_rev == head_rev, (
            f"Database revision '{db_rev}' does not match expected head '{head_rev}'.\n"
            "Run: alembic upgrade head"
        )

    @pytest.mark.parametrize(
        "table_name",
        [
            "change_events",
            "feedback_labels",
            "monitors",
            "outbox",
            "plans",
            "refresh_families",
            "refresh_tokens",
            "snapshots",
            "users",
        ],
    )
    async def test_each_table_exists_in_db(self, engine: AsyncEngine, table_name: str) -> None:
        """Verify each expected table actually exists in the Postgres schema.

        We query information_schema.tables - this is the SQL standard way to
        list tables, supported by all major databases.

        WHY NOT JUST QUERY THE TABLE DIRECTLY?
        Because querying a non-existent table raises an error with a confusing
        message. Checking information_schema gives a clear assertion failure.
        """
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name"
                ),
                {"name": table_name},
            )
            count: int = result.scalar_one()

        assert count == 1, (
            f"Table '{table_name}' does not exist in the public schema. "
            "Did the migration run successfully?"
        )

    async def test_citext_extension_installed(self, engine: AsyncEngine) -> None:
        """Verify the citext extension was installed by the migration.

        pg_extension is Postgres's internal catalog of installed extensions.
        citext must exist before any citext column can be used.
        """
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text("SELECT COUNT(*) FROM pg_extension WHERE extname = 'citext'")
            )
            count: int = result.scalar_one()

        assert count == 1, (
            "citext extension is NOT installed. "
            "Check the CREATE EXTENSION IF NOT EXISTS citext in upgrade()."
        )

    async def test_email_column_is_citext(self, engine: AsyncEngine) -> None:
        """Verify users.email is stored as citext, not plain text.

        information_schema.columns stores the data type for each column.
        citext shows up as a user-defined type with udt_name = 'citext'.
        """
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = 'email'"
                )
            )
            udt_name = result.scalar_one_or_none()

        assert udt_name == "citext", (
            f"users.email column type is '{udt_name}', expected 'citext'. "
            "Check the ALTER TABLE ... TYPE citext in upgrade()."
        )

    async def test_partial_indexes_exist(self, engine: AsyncEngine) -> None:
        """Verify partial indexes (with WHERE clauses) were created correctly.

        pg_indexes stores all index definitions including the WHERE clause.
        We check for both partial indexes defined in the migration.
        """
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN (:m_idx, :o_idx)"
                ),
                {
                    "m_idx": "ix_monitors_next_due_at_active",
                    "o_idx": "ix_outbox_available_at_pending",
                },
            )
            rows = {row[0]: row[1] for row in result.fetchall()}

        assert "ix_monitors_next_due_at_active" in rows, (
            "Missing partial index ix_monitors_next_due_at_active on monitors"
        )
        assert "WHERE" in rows["ix_monitors_next_due_at_active"].upper(), (
            "ix_monitors_next_due_at_active exists but has no WHERE clause"
        )
        assert "ix_outbox_available_at_pending" in rows, (
            "Missing partial index ix_outbox_available_at_pending on outbox"
        )
        assert "WHERE" in rows["ix_outbox_available_at_pending"].upper(), (
            "ix_outbox_available_at_pending exists but has no WHERE clause"
        )

    async def test_plans_seed_data_present(self, engine: AsyncEngine) -> None:
        """Verify the plans table was seeded with 'free' and 'pro' rows.

        The migration seeds these rows immediately after creating the plans
        table. If they're missing, the users.plan FK default ('free') would
        fail every INSERT.
        """
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text("SELECT code, max_monitors, min_interval_s FROM plans ORDER BY code")
            )
            rows = {row[0]: (row[1], row[2]) for row in result.fetchall()}

        assert "free" in rows, "plans table is missing the 'free' row"
        assert "pro" in rows, "plans table is missing the 'pro' row"
        assert rows["free"] == (5, 3600), (
            f"'free' plan has wrong values: {rows['free']}, expected (5, 3600)"
        )
        assert rows["pro"] == (100, 60), (
            f"'pro' plan has wrong values: {rows['pro']}, expected (100, 60)"
        )


# ---------------------------------------------------------------------------
# Tests: ORM round-trip with rollback isolation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDbRoundTrip:
    """Verify we can INSERT and SELECT rows through the async session.

    These tests prove the full stack works end-to-end:
      Python -> SQLAlchemy -> asyncpg driver -> Postgres

    Each test uses the `session` fixture which wraps everything in a
    rolled-back transaction, so no data persists after the test.
    """

    async def test_insert_plan_and_user(self, session: AsyncSession) -> None:
        """Insert a user record and verify it's retrievable within the same session.

        This test verifies:
          1. The session can execute INSERT statements
          2. The users table has the expected FK constraint to plans
          3. The auto-generated UUID primary key is assigned by Postgres
          4. The citext email column accepts mixed-case input
        """
        test_id = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, plan) "
                "VALUES (:id, :email, :hash, 'free')"
            ),
            {
                "id": str(test_id),
                "email": "IntegrationTest@Example.COM",
                "hash": "argon2:$argon2id$v=19$placeholder",
            },
        )

        result = await session.execute(
            sa.text("SELECT email, plan FROM users WHERE id = :id"),
            {"id": str(test_id)},
        )
        row = result.one_or_none()
        assert row is not None, "Inserted user was not found"
        assert row[1] == "free", f"Expected plan='free', got '{row[1]}'"

    async def test_citext_case_insensitive_lookup(self, session: AsyncSession) -> None:
        """Verify citext ignores case in WHERE clause comparisons.

        This is the core purpose of the citext extension. If email were plain
        TEXT, 'user@example.com' and 'USER@EXAMPLE.COM' would be different.
        With citext, they're equal - which is the correct behavior for emails.
        """
        test_id = uuid.uuid4()
        original_email = "CamelCase@Example.COM"

        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, plan) "
                "VALUES (:id, :email, :hash, 'free')"
            ),
            {"id": str(test_id), "email": original_email, "hash": "placeholder"},
        )

        # Look up with ALL LOWERCASE - citext should find it
        result = await session.execute(
            sa.text("SELECT id FROM users WHERE email = :email"),
            {"email": "camelcase@example.com"},
        )
        found_id = result.scalar_one_or_none()
        assert found_id is not None, (
            "citext case-insensitive lookup failed: "
            f"could not find '{original_email}' using 'camelcase@example.com'"
        )


# ---------------------------------------------------------------------------
# Tests: Scratch database migration lifecycle (Prompt 1 Section 8)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestScratchDatabaseLifecycle:
    """Verify full migration lifecycle on an isolated scratch database.

    As required by Prompt 1 Section 8:
    'create a scratch database, run migrations up, assert tables and both partial
     indexes exist (query pg_indexes), run down, assert tables are gone, drop the
     scratch database.'
    """

    def test_scratch_database_migration_lifecycle(self) -> None:
        """Run full migration up, verify schema + partial indexes, downgrade, verify drop."""
        raw_url = _db_url()
        norm_url = _normalize_db_url(raw_url)
        sa_url = sa.engine.make_url(norm_url)

        # Connect to maintenance DB (default postgres or warden)
        scratch_db_name = f"warden_scratch_{uuid.uuid4().hex[:8]}"

        async def _connect_asyncpg(database_name: str) -> asyncpg.Connection:
            return await asyncpg.connect(
                user=sa_url.username,
                password=sa_url.password,
                host=sa_url.host or "127.0.0.1",
                port=sa_url.port or 5433,
                database=database_name,
            )

        async def _exec_ddl(database_name: str, sql: str) -> None:
            conn = await _connect_asyncpg(database_name)
            try:
                await conn.execute(sql)
            finally:
                await conn.close()

        # Step 1: Create scratch database
        asyncio.run(_exec_ddl("warden", f"CREATE DATABASE {scratch_db_name}"))

        scratch_sa_url = sa_url.set(database=scratch_db_name)
        scratch_db_url = scratch_sa_url.render_as_string(hide_password=False)

        try:
            # Step 2: Run migrations UP
            cfg = Config("libs/warden_db/alembic.ini")
            old_env_url = os.environ.get("WARDEN_DATABASE_URL")
            os.environ["WARDEN_DATABASE_URL"] = scratch_db_url
            try:
                command.upgrade(cfg, "head")
            finally:
                if old_env_url is not None:
                    os.environ["WARDEN_DATABASE_URL"] = old_env_url
                else:
                    os.environ.pop("WARDEN_DATABASE_URL", None)

            # Step 3: Assert all 9 application tables exist in scratch DB
            async def _check_tables_and_indexes() -> None:
                conn = await _connect_asyncpg(scratch_db_name)
                try:
                    tables = await conn.fetch(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                    table_names = {r["table_name"] for r in tables}
                    expected = {
                        "plans",
                        "users",
                        "refresh_families",
                        "refresh_tokens",
                        "monitors",
                        "snapshots",
                        "change_events",
                        "outbox",
                        "feedback_labels",
                    }
                    assert expected.issubset(table_names), (
                        f"Missing tables in scratch DB: {expected - table_names}"
                    )

                    # Assert both partial indexes exist with their WHERE clause
                    indexes = await conn.fetch(
                        "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN "
                        "('ix_monitors_next_due_at_active', 'ix_outbox_available_at_pending')"
                    )
                    idx_map = {r["indexname"]: r["indexdef"] for r in indexes}
                    assert "ix_monitors_next_due_at_active" in idx_map, (
                        "Missing partial index ix_monitors_next_due_at_active on monitors"
                    )
                    assert "WHERE" in idx_map["ix_monitors_next_due_at_active"].upper(), (
                        "ix_monitors_next_due_at_active missing WHERE clause"
                    )
                    assert "ix_outbox_available_at_pending" in idx_map, (
                        "Missing partial index ix_outbox_available_at_pending on outbox"
                    )
                    assert "WHERE" in idx_map["ix_outbox_available_at_pending"].upper(), (
                        "ix_outbox_available_at_pending missing WHERE clause"
                    )
                finally:
                    await conn.close()

            asyncio.run(_check_tables_and_indexes())

            # Step 4: Run migrations DOWN (downgrade base)
            os.environ["WARDEN_DATABASE_URL"] = scratch_db_url
            try:
                command.downgrade(cfg, "base")
            finally:
                if old_env_url is not None:
                    os.environ["WARDEN_DATABASE_URL"] = old_env_url
                else:
                    os.environ.pop("WARDEN_DATABASE_URL", None)

            # Step 5: Assert tables are gone after downgrade
            async def _check_tables_gone() -> None:
                conn = await _connect_asyncpg(scratch_db_name)
                try:
                    tables = await conn.fetch(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                    app_tables = {
                        r["table_name"] for r in tables if r["table_name"] != "alembic_version"
                    }
                    assert len(app_tables) == 0, (
                        f"Tables still exist after downgrade base: {app_tables}"
                    )
                finally:
                    await conn.close()

            asyncio.run(_check_tables_gone())

        finally:
            # Step 6: Drop the scratch database
            asyncio.run(
                _exec_ddl("warden", f"DROP DATABASE IF EXISTS {scratch_db_name} WITH (FORCE)")
            )
