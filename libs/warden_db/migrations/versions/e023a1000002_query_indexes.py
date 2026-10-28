"""Indexes measured against the durable SLO and held-out promotion queries.

Local development uses ordinary CREATE INDEX. A production rollout on large
existing tables must use a separately scheduled CREATE INDEX CONCURRENTLY with
its transaction restrictions; do not surprise a busy primary with this migration.
"""

from alembic import op
import sqlalchemy as sa

revision = "e023a1000002"
down_revision = "e023a1000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_crawl_jobs_created_slo", "crawl_jobs", ["created_at"],
                    postgresql_include=["status", "completed_at"])
    op.create_index("ix_crawl_jobs_queued_age", "crawl_jobs", ["created_at"],
                    postgresql_where=sa.text("status = 'queued'"))
    op.create_index("ix_learning_owner_split_event", "learning_examples",
                    ["user_id", "split", "event_id"])


def downgrade() -> None:
    op.drop_index("ix_learning_owner_split_event", table_name="learning_examples")
    op.drop_index("ix_crawl_jobs_queued_age", table_name="crawl_jobs")
    op.drop_index("ix_crawl_jobs_created_slo", table_name="crawl_jobs")
