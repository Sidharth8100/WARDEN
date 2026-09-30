"""Unit tests for warden_db ORM models.

WHAT ARE UNIT TESTS?
====================
Unit tests test ONE small piece of code in isolation, without a real database,
network, or external service. They run in milliseconds and give instant feedback.

These tests verify:
  1. The ORM metadata contains exactly the right 9 tables
  2. Each table has a primary key defined
  3. CHECK constraints exist (e.g. role must be 'user' or 'admin')
  4. Partial indexes exist with the correct WHERE clauses
  5. No foreign key accidentally references the non-unique `id` column
     of the `snapshots` table

HOW DOES SQLALCHEMY METADATA WORK?
===================================
SQLAlchemy's Base.metadata is a registry of all the tables defined in your
Python classes. It's populated when Python imports the model classes.
By importing models and then inspecting Base.metadata, we can verify our
schema definitions without connecting to a real database.
"""

import pytest
from sqlalchemy import Index

# Importing models populates Base.metadata as a side effect.
# After this import, Base.metadata.tables contains all 9 table objects.
import warden_db.models  # noqa: F401
from warden_db.base import Base

# Expected tables in alphabetical order for deterministic comparison.
EXPECTED_TABLES = frozenset(
    {
        "change_events",
        "feedback_labels",
        "monitors",
        "outbox",
        "plans",
        "refresh_families",
        "refresh_tokens",
        "snapshots",
        "users",
    }
)


class TestModelRegistry:
    """Verify the ORM metadata contains the exact expected tables."""

    def test_exactly_nine_tables(self) -> None:
        """The metadata should contain exactly 9 tables - no more, no less.

        If someone adds a table without updating this test, it will fail,
        prompting them to consciously add it to EXPECTED_TABLES too.
        """
        actual = frozenset(Base.metadata.tables.keys())
        assert actual == EXPECTED_TABLES, (
            f"Table mismatch.\n"
            f"  Expected: {sorted(EXPECTED_TABLES)}\n"
            f"  Got:      {sorted(actual)}\n"
            f"  Extra:    {sorted(actual - EXPECTED_TABLES)}\n"
            f"  Missing:  {sorted(EXPECTED_TABLES - actual)}"
        )

    @pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
    def test_each_table_has_primary_key(self, table_name: str) -> None:
        """Every table must define at least one primary key column.

        A table without a PK is invalid in most databases and makes
        SQLAlchemy's ORM session management unreliable.
        """
        table = Base.metadata.tables[table_name]
        pk_cols = list(table.primary_key.columns)
        assert pk_cols, f"Table '{table_name}' has no primary key columns"


class TestCheckConstraints:
    """Verify named CHECK constraints exist on the metadata."""

    def _get_check_names(self, table_name: str) -> set[str]:
        """Get all CHECK constraint names for a table.

        c.name is typed as 'str | _NoneName | None' by SQLAlchemy's stubs
        because constraint names are optional. We filter to only non-None
        values and cast to str so mypy knows the set contains plain strings.
        """
        from sqlalchemy import CheckConstraint

        table = Base.metadata.tables[table_name]
        # `and c.name` filters out None; str(c.name) converts _NoneName -> str
        return {str(c.name) for c in table.constraints if isinstance(c, CheckConstraint) and c.name}

    def test_users_role_valid_check(self) -> None:
        """users.role must have the 'role_valid' CHECK constraint."""
        names = self._get_check_names("users")
        assert "ck_users_role_valid" in names, (
            f"Expected 'ck_users_role_valid' in users constraints, got: {names}"
        )

    def test_monitors_state_valid_check(self) -> None:
        """monitors.state must have the 'state_valid' CHECK constraint."""
        names = self._get_check_names("monitors")
        assert "ck_monitors_state_valid" in names, (
            f"Expected 'ck_monitors_state_valid' in monitors constraints, got: {names}"
        )

    def test_outbox_status_valid_check(self) -> None:
        """outbox.status must have the 'status_valid' CHECK constraint."""
        names = self._get_check_names("outbox")
        assert "ck_outbox_status_valid" in names, (
            f"Expected 'ck_outbox_status_valid' in outbox constraints, got: {names}"
        )

    def test_feedback_labels_label_valid_check(self) -> None:
        """feedback_labels.label must have the 'label_valid' CHECK constraint."""
        names = self._get_check_names("feedback_labels")
        assert "ck_feedback_labels_label_valid" in names, (
            f"Expected 'ck_feedback_labels_label_valid' in feedback_labels constraints, "
            f"got: {names}"
        )

    def test_snapshots_kind_valid_check(self) -> None:
        """snapshots.kind must have the 'kind_valid' CHECK constraint."""
        names = self._get_check_names("snapshots")
        assert "ck_snapshots_kind_valid" in names, (
            f"Expected 'ck_snapshots_kind_valid' in snapshots constraints, got: {names}"
        )


class TestPartialIndexes:
    """Verify partial indexes (indexes with WHERE clauses) exist on metadata."""

    def _get_index_info(self, table_name: str) -> dict[str, Index]:
        """Return a dict of {index_name: Index} for a table.

        idx.name is quoted_name (a str subclass). str() normalises it so the
        dict key is a plain str, making mypy and IDE lookups unambiguous.
        """
        table = Base.metadata.tables[table_name]
        # idx.name is always set (Index always has a name), so str() is safe
        return {str(idx.name): idx for idx in table.indexes if isinstance(idx, Index)}

    def test_monitors_partial_index_exists(self) -> None:
        """monitors should have the partial index on next_due_at WHERE state='active'."""
        indexes = self._get_index_info("monitors")
        assert "ix_monitors_next_due_at_active" in indexes, (
            f"Missing partial index 'ix_monitors_next_due_at_active' on monitors. "
            f"Found: {list(indexes.keys())}"
        )

    def test_monitors_partial_index_has_where(self) -> None:
        """The monitors partial index must have a WHERE clause."""
        indexes = self._get_index_info("monitors")
        idx = indexes.get("ix_monitors_next_due_at_active")
        assert idx is not None
        # dialect_kwargs stores Postgres-specific options like postgresql_where
        assert "postgresql_where" in idx.dialect_kwargs, (
            "Partial index 'ix_monitors_next_due_at_active' has no WHERE clause"
        )

    def test_outbox_partial_index_exists(self) -> None:
        """outbox should have the partial index on available_at WHERE status='pending'."""
        indexes = self._get_index_info("outbox")
        assert "ix_outbox_available_at_pending" in indexes, (
            f"Missing partial index 'ix_outbox_available_at_pending' on outbox. "
            f"Found: {list(indexes.keys())}"
        )

    def test_outbox_partial_index_has_where(self) -> None:
        """The outbox partial index must have a WHERE clause."""
        indexes = self._get_index_info("outbox")
        idx = indexes.get("ix_outbox_available_at_pending")
        assert idx is not None
        assert "postgresql_where" in idx.dialect_kwargs, (
            "Partial index 'ix_outbox_available_at_pending' has no WHERE clause"
        )


class TestNoForeignKeyOnSnapshotId:
    """Verify that snapshots.id has no incoming foreign keys.

    The snapshot table uses a COMPOSITE primary key (id, fetched_at).
    A foreign key can only reference a UNIQUE or PRIMARY KEY column.
    Since id alone is NOT unique, no table should have a FK to snapshots.id.
    This test catches accidental FK additions in future code.
    """

    def test_change_events_no_fk_to_snapshots(self) -> None:
        """change_events from/to_snapshot_id must NOT be foreign keys."""
        table = Base.metadata.tables["change_events"]
        fk_cols = {fk.column.table.name for fk in table.foreign_keys}
        assert "snapshots" not in fk_cols, (
            "change_events has a FK to snapshots - this is invalid because "
            "snapshots has a composite PK and id alone is not unique"
        )
