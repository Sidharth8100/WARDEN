"""Rule, delivery and learning records; Alembic remains the sole schema owner."""

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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from warden_db.base import Base


class RuleRevision(Base):
    __tablename__ = "rule_revisions"
    __table_args__ = (
        UniqueConstraint("monitor_id", "version"),
        CheckConstraint("version > 0", name="version_positive"),
        Index(
            "uq_rule_active_monitor", "monitor_id", unique=True, postgresql_where=text("enabled")
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RuleState(Base):
    __tablename__ = "rule_states"
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rule_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    next_allowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_id: Mapped[int | None] = mapped_column(BigInteger)


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"
    event_id: Mapped[int] = mapped_column(
        ForeignKey("change_events.id", ondelete="CASCADE"), primary_key=True
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rule_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    decision: Mapped[str] = mapped_column(Text)
    diagnostic: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    context_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AlertEndpoint(Base):
    __tablename__ = "alert_endpoints"
    __table_args__ = (
        UniqueConstraint("user_id", "name"),
        CheckConstraint("kind IN ('webhook', 'email')", name="kind_valid"),
        CheckConstraint("version > 0 AND failure_count >= 0", name="state_valid"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(Text, default="v1")
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probe_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "revision_id", "action_index"),
        Index("ix_deliveries_owner_created", "user_id", "created_at"),
        CheckConstraint(
            "status IN ('pending', 'leased', 'delivered', 'dead')", name="status_valid"
        ),
        CheckConstraint("attempts >= 0 AND retry_limit BETWEEN 1 AND 64", name="attempts_valid"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    outbox_id: Mapped[int] = mapped_column(ForeignKey("outbox.id", ondelete="CASCADE"), unique=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("change_events.id", ondelete="CASCADE"))
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rule_revisions.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    endpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alert_endpoints.id", ondelete="SET NULL")
    )
    action_index: Mapped[int] = mapped_column(Integer)
    endpoint_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    destination: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    secret_ref: Mapped[str | None] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(Text)
    traceparent: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_limit: Mapped[int] = mapped_column(Integer, default=8)
    lease_token: Mapped[uuid.UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint("delivery_id", "number"),
        CheckConstraint(
            "status IN ('claimed', 'abandoned', 'succeeded', 'failed')", name="status_valid"
        ),
        CheckConstraint("number > 0", name="number_positive"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deliveries.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    token: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningExample(Base):
    __tablename__ = "learning_examples"
    __table_args__ = (
        CheckConstraint("split IN ('train', 'holdout')", name="split_valid"),
        Index("ix_learning_owner_split_event", "user_id", "split", "event_id"),
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("change_events.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[bool] = mapped_column(Boolean)
    features: Mapped[dict[str, float]] = mapped_column(JSONB)
    split: Mapped[str] = mapped_column(Text)
    static_prediction: Mapped[bool] = mapped_column(Boolean)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (CheckConstraint("samples >= 0", name="samples_valid"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id"))
    weights: Mapped[dict[str, float]] = mapped_column(JSONB)
    prior_weights: Mapped[dict[str, float]] = mapped_column(JSONB)
    samples: Mapped[int] = mapped_column(Integer)
    feature_schema: Mapped[str] = mapped_column(Text, default="hashed-v1")
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ModelAlias(Base):
    __tablename__ = "model_aliases"
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    production_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id"))
    shadow_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id"))
    previous_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id"))


class ShadowScore(Base):
    __tablename__ = "shadow_scores"
    __table_args__ = (CheckConstraint("probability BETWEEN 0 AND 1", name="probability_valid"),)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("change_events.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), primary_key=True
    )
    probability: Mapped[float] = mapped_column(Double)
    shadow_prediction: Mapped[bool] = mapped_column(Boolean)
    production_prediction: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
