"""initial_schema

Revision ID: 2fdb8aa45d1d
Revises:
Create Date: 2026-10-08 19:03:09.003289

HAND-EDITS FROM AUTOGENERATE (each change explained):
======================================================

1. CREATE EXTENSION citext (first line of upgrade):
   The `users.email` column uses citext (Case-Insensitive TEXT), a Postgres
   extension. citext does not exist in a fresh database - we must CREATE it
   before any table that uses it is created. The extension is idempotent
   (IF NOT EXISTS), so running this migration twice is safe.

2. users.email column type changed to Text -> use execute() for citext:
   SQLAlchemy's sa.Text() maps to Postgres TEXT. citext is a Postgres-specific
   type not in SQLAlchemy's standard dialect, so we create the column with
   sa.Text() and then ALTER it to citext via raw SQL. This is the cleanest
   approach without adding the sqlalchemy-utils dependency.

   WHY NOT sqlalchemy-utils CIText?
   sqlalchemy-utils would add a dependency just for one column type. Using
   raw DDL for the one citext column is simpler and more transparent.

3. Seed the `plans` table immediately after it is created:
   The `users` table has a FK to `plans.code` with a server default of 'free'.
   If the plans table is empty when users are inserted, the FK check will fail.
   We seed the two canonical plans right after the table is created so there
   is never a window where the FK would be violated.
   Seeded: ('free', 5, 3600) and ('pro', 100, 60)

4. DESC indexes on snapshots and change_events:
   The autogenerate created plain ascending indexes. The spec and models define
   these as DESCENDING because queries almost always want "newest first".
   We pass postgresql_ops={'fetched_at': 'DESC'} and postgresql_ops={'detected_at': 'DESC'}
   to create proper B-tree index on the descending value.

5. DROP EXTENSION last in downgrade():
   citext must stay alive for as long as any citext column exists. Dropping
   the extension before dropping tables would fail. So we drop it last.

6. downgrade() drops in strict reverse dependency order:
   Autogenerate already does this (feedback_labels before snapshots, etc.).
   We verify: child tables are always dropped before parent tables they FK into.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2fdb8aa45d1d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the initial WARDEN schema to a fresh database."""

    # ------------------------------------------------------------------
    # HAND-EDIT 1: Install the citext Postgres extension.
    #
    # citext = Case-Insensitive TEXT. Used for users.email so that
    # lookups like "WHERE email = 'User@Example.com'" match
    # "user@example.com" without any LOWER() gymnastics.
    #
    # IF NOT EXISTS makes this idempotent (safe to run multiple times).
    # This must come BEFORE any table that has a citext column.
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # ------------------------------------------------------------------
    # outbox - standalone table (no FKs), safe to create first.
    # ------------------------------------------------------------------
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name=op.f("ck_outbox_status_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_outbox_idempotency_key")),
    )
    # HAND-EDIT 4 (partial): partial index - WHERE clause preserved from autogenerate.
    op.create_index(
        "ix_outbox_available_at_pending",
        "outbox",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ------------------------------------------------------------------
    # plans - referenced by users (FK), must exist first.
    # ------------------------------------------------------------------
    op.create_table(
        "plans",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("max_monitors", sa.Integer(), nullable=False),
        sa.Column("min_interval_s", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_plans")),
    )

    # HAND-EDIT 3: Seed the plans table.
    #
    # We insert the two canonical plans immediately after the table is created.
    # WHY HERE (not a separate migration)?
    # This data is schema-level configuration, not application data.  If the
    # table exists but has no rows, every user INSERT would fail the FK check
    # on users.plan (which defaults to 'free'). Seeding inside this migration
    # guarantees atomicity: either both the table AND its seed data exist, or
    # neither does (if the migration is rolled back).
    #
    # Plans seeded:
    #   free : max 5 monitors, minimum check interval 3600 seconds (1 hour)
    #   pro  : max 100 monitors, minimum check interval 60 seconds (1 minute)
    op.execute(
        """
        INSERT INTO plans (code, max_monitors, min_interval_s) VALUES
            ('free', 5, 3600),
            ('pro',  100, 60)
        """
    )

    # ------------------------------------------------------------------
    # users - depends on plans (FK on plan column).
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        # HAND-EDIT 2: email uses citext (case-insensitive text).
        # We create it as Text() first (SQLAlchemy doesn't know citext),
        # then ALTER it to citext via raw SQL immediately below.
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'user'"), nullable=False),
        sa.Column("plan", sa.Text(), server_default=sa.text("'free'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'admin')", name=op.f("ck_users_role_valid")),
        sa.ForeignKeyConstraint(["plan"], ["plans.code"], name=op.f("fk_users_plan_plans")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    # HAND-EDIT 2 (continued): Convert email column to citext.
    # citext is a Postgres extension type; SQLAlchemy core can't express it
    # directly, so we use raw DDL. USING is required because Postgres needs to
    # know how to cast TEXT -> citext (it's a trivial cast but must be explicit).
    op.execute("ALTER TABLE users ALTER COLUMN email TYPE citext USING email::citext")

    # ------------------------------------------------------------------
    # monitors - depends on users (FK on user_id).
    # ------------------------------------------------------------------
    op.create_table(
        "monitors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_canonical", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.LargeBinary(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("fetch_tier", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "needs_render", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("selector_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rule_src", sa.Text(), nullable=True),
        sa.Column("min_interval_s", sa.Integer(), nullable=False),
        sa.Column("max_interval_s", sa.Integer(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('active', 'paused', 'deleted')", name=op.f("ck_monitors_state_valid")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_monitors_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitors")),
        sa.UniqueConstraint("user_id", "url_hash", name=op.f("uq_monitors_user_id")),
    )
    # Plain B-tree index on domain for shard-routing (Day 11 workload).
    op.create_index("ix_monitors_domain", "monitors", ["domain"], unique=False)
    # HAND-EDIT 4: Partial index on next_due_at WHERE state='active'.
    # Only active monitors need to be polled, so excluding paused/deleted rows
    # keeps this index small and fast. The WHERE clause is preserved from the
    # model definition (Index(..., postgresql_where=...)).
    op.create_index(
        "ix_monitors_next_due_at_active",
        "monitors",
        ["next_due_at"],
        unique=False,
        postgresql_where=sa.text("state = 'active'"),
    )

    # ------------------------------------------------------------------
    # refresh_families - depends on users.
    # ------------------------------------------------------------------
    op.create_table(
        "refresh_families",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_families_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_families")),
    )

    # ------------------------------------------------------------------
    # change_events - depends on monitors.
    # ------------------------------------------------------------------
    op.create_table(
        "change_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("from_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("to_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("score", sa.Double(), nullable=True),
        sa.Column("meaningful", sa.Boolean(), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ops", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extracted", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("classifier_version", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            name=op.f("fk_change_events_monitor_id_monitors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_events")),
    )
    # HAND-EDIT 4: DESC index - queries always want newest events first.
    # postgresql_ops specifies the sort order per column in the index.
    # Without DESC, Postgres uses ASC which requires a backward index scan
    # for ORDER BY detected_at DESC queries (slower on large tables).
    op.create_index(
        "ix_change_events_monitor_detected",
        "change_events",
        ["monitor_id", "detected_at"],
        unique=False,
        postgresql_ops={"detected_at": "DESC"},
    )

    # ------------------------------------------------------------------
    # refresh_tokens - depends on refresh_families (self-referential FK too).
    # ------------------------------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["refresh_families.id"],
            name=op.f("fk_refresh_tokens_family_id_refresh_families"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["refresh_tokens.id"],
            name=op.f("fk_refresh_tokens_parent_id_refresh_tokens"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )

    # ------------------------------------------------------------------
    # snapshots - depends on monitors.
    # Composite PK (id, fetched_at) - required for future month-partitioning.
    # See model docstring for why base_snapshot_id has no FK.
    # ------------------------------------------------------------------
    op.create_table(
        "snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("base_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.LargeBinary(), nullable=False),
        sa.Column("simhash", sa.BigInteger(), nullable=True),
        sa.Column("blob_ref", sa.Text(), nullable=True),
        sa.Column("screenshot_ref", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("tier", sa.SmallInteger(), nullable=True),
        sa.CheckConstraint("kind IN ('keyframe', 'delta')", name=op.f("ck_snapshots_kind_valid")),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            name=op.f("fk_snapshots_monitor_id_monitors"),
            ondelete="CASCADE",
        ),
        # Composite PK includes fetched_at so Postgres can use it as the
        # partition key when we range-partition by month (future Day N).
        sa.PrimaryKeyConstraint("id", "fetched_at", name=op.f("pk_snapshots")),
    )
    # HAND-EDIT 4: DESC index - "last N snapshots for this monitor" is the
    # dominant query pattern; DESC lets Postgres scan from newest to oldest
    # without a sort step.
    op.create_index(
        "ix_snapshots_monitor_fetched",
        "snapshots",
        ["monitor_id", "fetched_at"],
        unique=False,
        postgresql_ops={"fetched_at": "DESC"},
    )

    # ------------------------------------------------------------------
    # feedback_labels - depends on change_events and users.
    # Composite PK (event_id, user_id) - one label per user per event.
    # ------------------------------------------------------------------
    op.create_table(
        "feedback_labels",
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("feature_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "label IN ('useful', 'noise')", name=op.f("ck_feedback_labels_label_valid")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["change_events.id"],
            name=op.f("fk_feedback_labels_event_id_change_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_feedback_labels_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", "user_id", name=op.f("pk_feedback_labels")),
    )


def downgrade() -> None:
    """Remove ALL WARDEN tables in strict reverse dependency order.

    Order matters: child tables (those with FKs) must be dropped BEFORE
    the parent tables they reference. Postgres enforces FK constraints even
    during DROP TABLE unless CASCADE is used - we don't use CASCADE here to
    keep each step explicit.

    Final step: DROP EXTENSION citext.
    citext must stay installed for as long as any citext column exists. We
    only drop it after ALL tables are gone.
    """
    # Tables that depend on change_events must go first.
    op.drop_table("feedback_labels")

    # Tables that depend on monitors/snapshots (no FKs between them).
    op.drop_index("ix_snapshots_monitor_fetched", table_name="snapshots")
    op.drop_table("snapshots")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_change_events_monitor_detected", table_name="change_events")
    op.drop_table("change_events")
    op.drop_table("refresh_families")

    # monitors depends on users; drop monitors indexes first, then the table.
    op.drop_index(
        "ix_monitors_next_due_at_active",
        table_name="monitors",
        postgresql_where=sa.text("state = 'active'"),
    )
    op.drop_index("ix_monitors_domain", table_name="monitors")
    op.drop_table("monitors")

    # users depends on plans; drop users first.
    op.drop_table("users")
    op.drop_table("plans")

    # outbox is standalone.
    op.drop_index(
        "ix_outbox_available_at_pending",
        table_name="outbox",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("outbox")

    # HAND-EDIT 5: Drop citext extension LAST.
    # The extension provides the citext type used by users.email.
    # Dropping it before dropping the users table would fail.
    op.execute("DROP EXTENSION IF EXISTS citext")
