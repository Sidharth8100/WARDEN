"""Durable crawl ledger and global scheduler fence.

Revision ID: e021a1000001
Revises: d7192f44a301
"""

import sqlalchemy as sa
from alembic import op

revision = "e021a1000001"
down_revision = "d7192f44a301"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduler_fences",
        sa.Column("role", sa.Text(), primary_key=True),
        sa.Column("token", sa.BigInteger(), nullable=False),
    )
    op.execute("INSERT INTO scheduler_fences (role, token) VALUES ('scheduler', 0)")
    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("monitor_id", sa.Uuid(), sa.ForeignKey("monitors.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("changed", sa.Boolean()),
        sa.Column("error", sa.Text()),
        sa.Column("snapshot_id", sa.BigInteger(), unique=True),
        sa.Column("event_id", sa.BigInteger(), unique=True),
        sa.CheckConstraint("status IN ('queued', 'completed', 'failed')",
                           name="ck_crawl_jobs_status_valid"),
    )
    op.create_index("ix_crawl_jobs_monitor_completed", "crawl_jobs", ["monitor_id", "completed_at"])


def downgrade() -> None:
    op.drop_table("crawl_jobs")
    op.drop_table("scheduler_fences")
