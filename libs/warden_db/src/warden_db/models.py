"""ORM models for the WARDEN platform.

All 9 tables live here for Day 1. As the schema grows, split into sub-modules
(e.g. models/auth.py, models/monitoring.py) and re-export from models/__init__.py.

Design notes:
- All timestamps use DateTime(timezone=True) - we never store naive datetimes.
- UUID primary keys for API-exposed entities (generated Python-side with uuid4 so the
  application knows the ID before the INSERT, avoiding an extra DB round-trip).
- bigint GENERATED ALWAYS AS IDENTITY for high-volume append-only tables (snapshots,
  change_events, outbox) because auto-increment is faster than UUID for sequential scans.
- citext extension is required for case-insensitive email storage; the migration handles
  CREATE EXTENSION IF NOT EXISTS citext before this table is created.
- Named CHECK constraints via the MetaData naming_convention so every constraint gets a
  deterministic name (ck_<table>_<constraint_name>) regardless of environment.
- No module-level mutable globals; models are pure data classes.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from warden_db.base import Base
from warden_db.extensions import AlertEndpoint as AlertEndpoint
from warden_db.extensions import Delivery as Delivery
from warden_db.extensions import DeliveryAttempt as DeliveryAttempt
from warden_db.extensions import LearningExample as LearningExample
from warden_db.extensions import ModelAlias as ModelAlias
from warden_db.extensions import ModelVersion as ModelVersion
from warden_db.extensions import RuleEvaluation as RuleEvaluation
from warden_db.extensions import RuleRevision as RuleRevision
from warden_db.extensions import RuleState as RuleState
from warden_db.extensions import ShadowScore as ShadowScore

# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


class Plan(Base):
    """Subscription plan definitions - referenced by users.plan."""

    __tablename__ = "plans"

    # Text primary key (e.g. 'free', 'pro') - not UUID because plans are a
    # small, human-readable lookup table, not an API-exposed entity.
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    max_monitors: Mapped[int] = mapped_column(Integer, nullable=False)
    min_interval_s: Mapped[int] = mapped_column(Integer, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="plan_rel")


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


class User(Base):
    """Platform user account."""

    __tablename__ = "users"
    __table_args__ = (
        # Named CHECK so Alembic can detect drifts and the DBA can reference
        # the constraint by a stable name.
        CheckConstraint("role IN ('user', 'admin')", name="role_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # citext column - case-insensitive text handled by the Postgres extension.
    # We declare it as Text here; the migration adds the extension and the
    # column type is citext at the DB level via server_default DDL.
    # Using sqlalchemy-utils CIText or raw DDL in migration (see migration note).
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'user'"))
    plan: Mapped[str] = mapped_column(
        Text,
        ForeignKey("plans.code"),
        nullable=False,
        server_default=text("'free'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    plan_rel: Mapped["Plan"] = relationship(back_populates="users")
    refresh_families: Mapped[list["RefreshFamily"]] = relationship(back_populates="user")
    monitors: Mapped[list["Monitor"]] = relationship(back_populates="user")
    feedback_labels: Mapped[list["FeedbackLabel"]] = relationship(back_populates="user")
    tags: Mapped[list["Tag"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")
    idempotency_keys: Mapped[list["IdempotencyKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# refresh_families
# ---------------------------------------------------------------------------


class RefreshFamily(Base):
    """Groups refresh tokens into rotation families.

    WHY families?  Token rotation: when a refresh token is used, it is marked
    used and a child token is issued.  If an old token is replayed (theft
    indicator), the entire family is revoked.  Storing a family ID lets us
    invalidate all tokens in the chain with one UPDATE.
    """

    __tablename__ = "refresh_families"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="refresh_families")
    tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="family")


# ---------------------------------------------------------------------------
# refresh_tokens
# ---------------------------------------------------------------------------


class RefreshToken(Base):
    """Individual refresh token in a rotation chain."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("refresh_families.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    # Self-referential FK: tracks which token issued this one.
    # nullable because the first token in a family has no parent.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id"), nullable=True
    )
    # used_at is NULL until the token is redeemed (nullable by design).
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    family: Mapped["RefreshFamily"] = relationship(back_populates="tokens")


# ---------------------------------------------------------------------------
# monitors
# ---------------------------------------------------------------------------


class Monitor(Base):
    """A URL that a user wants to track for changes."""

    __tablename__ = "monitors"
    __table_args__ = (
        CheckConstraint("state IN ('active', 'paused', 'deleted')", name="state_valid"),
        UniqueConstraint("user_id", "url_hash"),
        # Regular B-tree index on domain for shard-routing lookups (Day 11).
        Index("ix_monitors_domain", "domain"),
        # Partial index: only rows where state='active' enter this index.
        # This keeps the index small and the scheduler query fast -
        # "give me the N monitors due soonest" only ever looks at active ones.
        Index(
            "ix_monitors_next_due_at_active",
            "next_due_at",
            postgresql_where=text("state = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # NOTE: domain holds the REGISTRABLE domain (eTLD+1, via tldextract,
    # e.g. example.co.uk, not www.example.co.uk). Day 11's sharding depends on this.
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    fetch_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    needs_render: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    selector_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    rule_src: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_interval_s: Mapped[int] = mapped_column(Integer, nullable=False)
    max_interval_s: Mapped[int] = mapped_column(Integer, nullable=False)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # updated_at: set by the DB on INSERT and updated by SQLAlchemy on every
    # UPDATE via onupdate. Using server_default for INSERT consistency.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    user: Mapped["User"] = relationship(back_populates="monitors")
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="monitor")
    change_events: Mapped[list["ChangeEvent"]] = relationship(back_populates="monitor")
    tags: Mapped[list["Tag"]] = relationship(secondary="monitor_tags", back_populates="monitors")


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


class Snapshot(Base):
    """One fetched copy of a monitored URL.

    Composite PK (id, fetched_at):
      Postgres requires the partition key to be part of the primary key when
      range-partitioning by month (Day N).  We add fetched_at to the PK now
      so the partition migration is non-destructive.

    base_snapshot_id has NO foreign key:
      A FK must reference a UNIQUE or PRIMARY KEY column.  Because id alone is
      NOT unique in this table (the PK is (id, fetched_at)), a FK on id would
      be invalid.  We enforce referential integrity in application code instead.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        CheckConstraint("kind IN ('keyframe', 'delta')", name="kind_valid"),
        # Covering index for "last N snapshots for monitor X" query pattern.
        # DESC because we almost always want newest-first.
        Index("ix_snapshots_monitor_fetched", "monitor_id", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        # GENERATED ALWAYS AS IDENTITY is DDL-level; mapped as autoincrement.
        autoincrement=True,
        primary_key=True,
    )
    # fetched_at is part of the composite PK - see class docstring.
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, primary_key=True
    )
    monitor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # NO FK - see class docstring.
    base_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    simhash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    blob_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    monitor: Mapped["Monitor"] = relationship(back_populates="snapshots")


# ---------------------------------------------------------------------------
# change_events
# ---------------------------------------------------------------------------


class ChangeEvent(Base):
    """A detected difference between two consecutive snapshots."""

    __tablename__ = "change_events"
    __table_args__ = (Index("ix_change_events_monitor_detected", "monitor_id", "detected_at"),)

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    monitor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False
    )
    # NO FKs on from/to snapshot IDs - same reason as base_snapshot_id above:
    # the snapshots PK is composite, so a single-column FK is not valid.
    from_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Double, nullable=True)
    meaningful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    features: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    ops: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    extracted: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    classifier_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    monitor: Mapped["Monitor"] = relationship(back_populates="change_events")
    feedback_labels: Mapped[list["FeedbackLabel"]] = relationship(back_populates="change_event")


# ---------------------------------------------------------------------------
# outbox
# ---------------------------------------------------------------------------


class Outbox(Base):
    """Transactional outbox for reliable event delivery.

    The outbox pattern: write events to this table in the same DB transaction
    as the business operation.  A separate relay process reads pending rows and
    publishes them to the message bus, then marks them sent.  This guarantees
    at-least-once delivery even if the relay crashes.
    """

    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="status_valid"),
        # Partial index: only pending rows need to be polled.
        # Delivered rows are excluded, keeping the relay's query fast.
        Index(
            "ix_outbox_available_at_pending",
            "available_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ---------------------------------------------------------------------------
# feedback_labels
# ---------------------------------------------------------------------------


class FeedbackLabel(Base):
    """Human label on a change event (useful / noise) for ML training data.

    Composite PK (event_id, user_id): one label per user per event.
    No separate surrogate key needed - the combination is naturally unique
    and small enough to be a good clustered index key.
    """

    __tablename__ = "feedback_labels"
    __table_args__ = (CheckConstraint("label IN ('useful', 'noise')", name="label_valid"),)

    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("change_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    feature_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    change_event: Mapped["ChangeEvent"] = relationship(back_populates="feedback_labels")
    user: Mapped["User"] = relationship(back_populates="feedback_labels")


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------


class Tag(Base):
    """User-defined tag for organizing monitors."""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    user: Mapped["User"] = relationship(back_populates="tags")
    monitors: Mapped[list["Monitor"]] = relationship(
        secondary="monitor_tags", back_populates="tags"
    )


# ---------------------------------------------------------------------------
# monitor_tags
# ---------------------------------------------------------------------------


class MonitorTag(Base):
    """Association table connecting monitors to tags (many-to-many)."""

    __tablename__ = "monitor_tags"

    monitor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """Security and operational audit trail."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_user_id_created_at", "user_id", text("created_at DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)  # type: ignore[type-arg]
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    user: Mapped["User | None"] = relationship(back_populates="audit_logs")


# ---------------------------------------------------------------------------
# idempotency_keys
# ---------------------------------------------------------------------------


class IdempotencyKey(Base):
    """Idempotency record for non-GET API mutation requests.

    NOTE: this table currently has no TTL/cleanup background job;
    flagged as a known gap for a later day.
    """

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    user: Mapped["User"] = relationship(back_populates="idempotency_keys")


class SchedulerFence(Base):
    """One shared high-water term; lock this row before any scheduler DB writes."""

    __tablename__ = "scheduler_fences"
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    token: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CrawlJob(Base):
    """Durable accepted-job ledger and idempotent processing receipt.

    Lock its monitor first, then this row. Status changes and business effects
    commit together. Payloads from the untrusted fetcher must match this ledger.
    """

    __tablename__ = "crawl_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'completed', 'failed')", name="status_valid"),
        Index("ix_crawl_jobs_monitor_completed", "monitor_id", "completed_at"),
        Index(
            "ix_crawl_jobs_created_slo", "created_at", postgresql_include=["status", "completed_at"]
        ),
        Index("ix_crawl_jobs_queued_age", "created_at", postgresql_where=text("status = 'queued'")),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    monitor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    event_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
