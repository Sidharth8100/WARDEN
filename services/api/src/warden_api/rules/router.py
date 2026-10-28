"""Owner-scoped rule compilation, endpoint registration and delivery recovery."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from warden_rules.context import deserialize
from warden_rules.evaluator import evaluate
from warden_rules.language import RuleError, compile_rule, pretty

from warden_api.auth.dependencies import get_current_user
from warden_api.deps import get_db
from warden_api.errors import APIError
from warden_db.learning import add_feedback, promote, rollback
from warden_db.models import (
    AlertEndpoint,
    AuditLog,
    ChangeEvent,
    Delivery,
    DeliveryAttempt,
    IdempotencyKey,
    ModelAlias,
    ModelVersion,
    Monitor,
    Outbox,
    RuleRevision,
    User,
)
from warden_net.ssrf_check import validate_url_is_safe

router = APIRouter(prefix="/api", tags=["rules and learning"])
type Session = Annotated[AsyncSession, Depends(get_db)]
type Owner = Annotated[User, Depends(get_current_user)]


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(max_length=16_384)


class RuleUpdate(RuleInput):
    monitor_version: int = Field(ge=1)
    enabled: bool = True


class EndpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    kind: Literal["webhook", "email"]
    target: str = Field(min_length=1, max_length=2048)
    secret_ref: str | None = Field(default=None, pattern=r"^WARDEN_WEBHOOK_KEY_[A-Z0-9_]+$")
    key_id: str = Field(default="v1", pattern=r"^[a-zA-Z0-9_-]{1,32}$")


class EndpointUpdate(EndpointInput):
    version: int = Field(ge=1)
    enabled: bool = True


async def validate_destination(kind: str, target: str) -> None:
    if any(ord(c) < 32 for c in target):
        raise APIError(422, "Invalid destination", "Destination contains control characters")
    if kind == "email":
        name, address = parseaddr(target)
        if name or address != target or target.count("@") != 1 or len(target) > 254:
            raise APIError(422, "Invalid destination", "Expected one email address")
        return
    try:
        parsed = urlsplit(target)
    except ValueError as exc:
        raise APIError(422, "Invalid destination", "Malformed webhook URL") from exc
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise APIError(
            422, "Invalid destination", "Webhook requires HTTPS without userinfo or fragment"
        )
    check = await validate_url_is_safe(target)
    if not check.safe:
        raise APIError(422, "Invalid destination", "Webhook destination is not allowed")


async def commit_endpoint(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise APIError(409, "Conflict", "Destination name already exists") from exc
        raise


@router.post("/rules/validate")
async def validate_rule(body: RuleInput, user: Owner) -> dict[str, object]:
    checked = compile_rule(body.source)
    return {"valid": True, "canonical": pretty(checked.program), "digest": checked.digest}


@router.put("/monitors/{monitor_id}/rule")
async def set_rule(
    monitor_id: uuid.UUID, body: RuleUpdate, session: Session, user: Owner
) -> dict[str, object]:
    monitor = await session.scalar(
        select(Monitor)
        .where(Monitor.id == monitor_id, Monitor.user_id == user.id, Monitor.state != "deleted")
        .with_for_update()
    )
    if monitor is None:
        raise APIError(404, "Not Found", "Monitor not found")
    if monitor.version != body.monitor_version:
        raise APIError(409, "Conflict", "Monitor version changed")
    checked = compile_rule(body.source)
    for action in checked.program.actions:
        if action.kind != "tag" and body.enabled:
            endpoint = await session.scalar(
                select(AlertEndpoint).where(
                    AlertEndpoint.user_id == user.id,
                    AlertEndpoint.name == action.target,
                    AlertEndpoint.kind == action.kind,
                    AlertEndpoint.enabled.is_(True),
                )
            )
            if endpoint is None:
                raise RuleError("endpoint", "Register an owned destination first", action.span)
    active = await session.scalar(
        select(RuleRevision).where(
            RuleRevision.monitor_id == monitor_id, RuleRevision.enabled.is_(True)
        )
    )
    if active and active.digest == checked.digest and body.enabled:
        return {
            "revision_id": str(active.id),
            "version": active.version,
            "monitor_version": monitor.version,
        }
    version = (
        await session.scalar(
            select(func.max(RuleRevision.version)).where(RuleRevision.monitor_id == monitor_id)
        )
        or 0
    ) + 1
    await session.execute(
        update(RuleRevision).where(RuleRevision.monitor_id == monitor_id).values(enabled=False)
    )
    row = RuleRevision(
        monitor_id=monitor_id,
        user_id=user.id,
        version=version,
        source=body.source,
        digest=checked.digest,
        enabled=body.enabled,
    )
    session.add(row)
    monitor.rule_src = body.source if body.enabled else None
    monitor.version += 1
    session.add(
        AuditLog(
            user_id=user.id,
            action="rule.revised",
            resource_type="monitor",
            resource_id=str(monitor_id),
        )
    )
    await session.commit()
    return {"revision_id": str(row.id), "version": row.version, "monitor_version": monitor.version}


@router.post("/alert-endpoints", status_code=201)
async def create_endpoint(body: EndpointInput, session: Session, user: Owner) -> dict[str, object]:
    await validate_destination(body.kind, body.target)
    if body.kind == "webhook" and body.secret_ref is None:
        raise APIError(
            422, "Invalid destination", "Webhook needs an operator-provisioned key reference"
        )
    exists = await session.scalar(
        select(AlertEndpoint.id).where(
            AlertEndpoint.user_id == user.id, AlertEndpoint.name == body.name
        )
    )
    if exists:
        raise APIError(409, "Conflict", "Destination name already exists")
    row = AlertEndpoint(user_id=user.id, **body.model_dump())
    session.add(row)
    await commit_endpoint(session)
    return {"id": str(row.id), "name": row.name, "version": row.version}


@router.put("/alert-endpoints/{endpoint_id}")
async def update_endpoint(
    endpoint_id: uuid.UUID, body: EndpointUpdate, session: Session, user: Owner
) -> dict[str, object]:
    row = await session.scalar(
        select(AlertEndpoint)
        .where(AlertEndpoint.id == endpoint_id, AlertEndpoint.user_id == user.id)
        .with_for_update()
    )
    if row is None:
        raise APIError(404, "Not Found", "Destination not found")
    if row.version != body.version:
        raise APIError(409, "Conflict", "Destination version changed")
    if row.kind != body.kind:
        raise APIError(422, "Invalid destination", "Destination kind is immutable; create another")
    if body.enabled:
        await validate_destination(body.kind, body.target)
        if body.kind == "webhook" and body.secret_ref is None:
            raise APIError(422, "Invalid destination", "Webhook needs an operator key reference")
    row.name, row.kind, row.target = body.name, body.kind, body.target
    row.secret_ref, row.key_id, row.enabled = body.secret_ref, body.key_id, body.enabled
    row.version += 1
    session.add(
        AuditLog(
            user_id=user.id,
            action="destination.revised",
            resource_type="endpoint",
            resource_id=str(row.id),
        )
    )
    await commit_endpoint(session)
    return {"id": str(row.id), "version": row.version, "enabled": row.enabled}


@router.get("/deliveries")
async def deliveries(
    session: Session,
    user: Owner,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_id: uuid.UUID | None = None,
) -> dict[str, object]:
    query = select(Delivery).where(Delivery.user_id == user.id)
    if before:
        if before.tzinfo is None:
            raise APIError(422, "Invalid cursor", "Cursor time requires timezone")
        from sqlalchemy import tuple_

        if before_id is None:
            raise APIError(422, "Invalid cursor", "Cursor requires time and delivery ID")
        query = query.where(tuple_(Delivery.created_at, Delivery.id) < (before, before_id))
    rows = (
        await session.scalars(
            query.order_by(Delivery.created_at.desc(), Delivery.id.desc()).limit(limit + 1)
        )
    ).all()
    selected = rows[:limit]
    return {
        "items": [
            {
                "id": str(row.id),
                "event_id": row.event_id,
                "kind": row.kind,
                "status": row.status,
                "attempts": row.attempts,
                "error": row.last_error,
                "created_at": row.created_at.isoformat(),
            }
            for row in selected
        ],
        "next_cursor": {
            "before": selected[-1].created_at.isoformat(),
            "before_id": str(selected[-1].id),
        }
        if len(rows) > limit
        else None,
    }


@router.get("/deliveries/{delivery_id}/attempts")
async def attempts(delivery_id: uuid.UUID, session: Session, user: Owner) -> dict[str, object]:
    row = await session.scalar(
        select(Delivery).where(Delivery.id == delivery_id, Delivery.user_id == user.id)
    )
    if row is None:
        raise APIError(404, "Not Found", "Delivery not found")
    rows = (
        await session.scalars(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.delivery_id == delivery_id)
            .order_by(DeliveryAttempt.number)
            .limit(64)
        )
    ).all()
    return {
        "items": [
            {
                "number": x.number,
                "status": x.status,
                "http_status": x.http_status,
                "error": x.error,
                "started_at": x.started_at.isoformat(),
            }
            for x in rows
        ]
    }


@router.post("/deliveries/{delivery_id}/redeliver", status_code=202)
async def redeliver(
    delivery_id: uuid.UUID,
    session: Session,
    user: Owner,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> dict[str, object]:
    key = f"redeliver:{user.id}:{idempotency_key}"
    lock = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    saved = await session.get(IdempotencyKey, key)
    request_hash = hashlib.sha256(str(delivery_id).encode()).hexdigest()
    if saved:
        if saved.request_hash != request_hash or saved.user_id != user.id:
            raise APIError(409, "Conflict", "Idempotency key belongs to a different request")
        return saved.response_body or {}
    row = await session.scalar(
        select(Delivery)
        .where(Delivery.id == delivery_id, Delivery.user_id == user.id)
        .with_for_update()
    )
    if row is None:
        raise APIError(404, "Not Found", "Delivery not found")
    if row.status != "dead" or row.retry_limit >= 64:
        raise APIError(
            409, "Conflict", "Only dead deliveries below the operator retry cap can be redelivered"
        )
    endpoint = await session.get(AlertEndpoint, row.endpoint_id) if row.endpoint_id else None
    if endpoint is None or endpoint.user_id != user.id or not endpoint.enabled:
        raise APIError(409, "Conflict", "Destination unavailable")
    await validate_destination(endpoint.kind, endpoint.target)
    row.destination, row.endpoint_version = endpoint.target, endpoint.version
    row.secret_ref, row.key_id = endpoint.secret_ref, endpoint.key_id
    row.status, row.last_error, row.lease_token, row.lease_expires_at = "pending", None, None, None
    row.retry_limit += 8
    outbox = await session.get(Outbox, row.outbox_id)
    if outbox is None:
        raise RuntimeError("delivery outbox missing")
    outbox.status, outbox.available_at = "pending", datetime.now(UTC)
    response: dict[str, object] = {"delivery_id": str(row.id), "status": "pending"}
    session.add(
        IdempotencyKey(
            key=key,
            user_id=user.id,
            request_hash=request_hash,
            response_status=202,
            response_body=response,
        )
    )
    session.add(
        AuditLog(
            user_id=user.id,
            action="delivery.redeliver",
            resource_type="delivery",
            resource_id=str(row.id),
        )
    )
    await session.execute(text("SELECT pg_notify('warden_alerts', '')"))
    await session.commit()
    return response


class ReplayInput(RuleInput):
    monitor_id: uuid.UUID
    limit: int = Field(default=50, ge=1, le=100)


@router.post("/rules/dry-run")
async def dry_run(body: ReplayInput, session: Session, user: Owner) -> dict[str, object]:
    monitor = await session.scalar(
        select(Monitor).where(Monitor.id == body.monitor_id, Monitor.user_id == user.id)
    )
    if monitor is None:
        raise APIError(404, "Not Found", "Monitor not found")
    monitor_identity = monitor.id
    checked = compile_rule(body.source)
    events = list(
        reversed(
            (
                await session.scalars(
                    select(ChangeEvent)
                    .where(ChangeEvent.monitor_id == monitor_identity)
                    .order_by(ChangeEvent.id.desc())
                    .limit(body.limit)
                )
            ).all()
        )
    )

    class Historical:
        def __init__(self, event: ChangeEvent):
            self.event = event

        async def count(self, seconds: int) -> int:
            return (
                await session.scalar(
                    select(func.count())
                    .select_from(ChangeEvent)
                    .where(
                        ChangeEvent.monitor_id == monitor_identity,
                        ChangeEvent.id < self.event.id,
                        ChangeEvent.meaningful.is_(True),
                        ChangeEvent.detected_at
                        >= self.event.detected_at - timedelta(seconds=seconds),
                        ChangeEvent.detected_at <= self.event.detected_at,
                    )
                )
                or 0
            )

    decisions: list[dict[str, object]] = []
    next_allowed: datetime | None = None
    for event in events:
        decision = "unavailable_context"
        if event.extracted and event.meaningful:
            try:
                matched = await evaluate(checked, deserialize(event.extracted), Historical(event))
                decision = "no_match"
                if matched:
                    decision = (
                        "cooldown"
                        if next_allowed and event.detected_at < next_allowed
                        else "matched"
                    )
                    if decision == "matched":
                        next_allowed = event.detected_at + timedelta(
                            seconds=checked.program.cooldown_s
                        )
            except (RuleError, ValueError):
                decision = "error"
        decisions.append({"event_id": event.id, "decision": decision})
    return {
        "items": decisions,
        "historical_cooldown_before_replay_known": False,
        "writes_actions_or_cooldown": False,
    }


class FeedbackInput(BaseModel):
    useful: bool


@router.post("/events/{event_id}/feedback")
async def feedback(
    event_id: int, body: FeedbackInput, session: Session, user: Owner
) -> dict[str, object]:
    event = await session.scalar(
        select(ChangeEvent)
        .join(Monitor)
        .where(ChangeEvent.id == event_id, Monitor.user_id == user.id)
    )
    if event is None:
        raise APIError(404, "Not Found", "Event not found")
    try:
        version = await add_feedback(session, event, user.id, body.useful)
    except ValueError as exc:
        raise APIError(409, "Conflict", "Feedback is immutable; existing label differs") from exc
    await session.commit()
    return {"event_id": event.id, "shadow_model_id": str(version.id) if version else None}


@router.get("/models")
async def models(session: Session, user: Owner) -> dict[str, object]:
    pointer = await session.get(ModelAlias, str(user.id))
    rows = (
        await session.scalars(
            select(ModelVersion)
            .where(ModelVersion.user_id == user.id)
            .order_by(ModelVersion.created_at.desc())
            .limit(100)
        )
    ).all()
    return {
        "production": str(pointer.production_id)
        if pointer and pointer.production_id
        else "static-v2",
        "shadow": str(pointer.shadow_id) if pointer and pointer.shadow_id else None,
        "items": [
            {
                "id": str(x.id),
                "samples": x.samples,
                "created_at": x.created_at.isoformat(),
                "feature_schema": x.feature_schema,
            }
            for x in rows
        ],
    }


@router.post("/models/promote")
async def promote_model(session: Session, user: Owner) -> dict[str, object]:
    try:
        result = await promote(session, user.id)
    except ValueError as exc:
        raise APIError(409, "Conflict", "Candidate or held-out data unavailable") from exc
    session.add(
        AuditLog(
            user_id=user.id,
            action="model.promotion_decision",
            resource_type="model",
            resource_id=str(result["candidate_id"]),
            metadata_=result,
        )
    )
    await session.commit()
    return result


@router.post("/models/rollback")
async def rollback_model(session: Session, user: Owner) -> dict[str, object]:
    try:
        await rollback(session, user.id)
    except ValueError as exc:
        raise APIError(409, "Conflict", "No production model to roll back") from exc
    session.add(AuditLog(user_id=user.id, action="model.rollback", resource_type="model"))
    await session.commit()
    return {"rolled_back": True}
