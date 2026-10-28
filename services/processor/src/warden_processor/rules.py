"""Monitor-serialized evaluation and alert intent staging in the event transaction."""

import json
import uuid
from datetime import timedelta
from functools import lru_cache

from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from warden_observe.telemetry import carrier
from warden_rules.evaluator import Value, evaluate
from warden_rules.language import CheckedProgram, RuleError, compile_rule

from warden_db.models import (
    AlertEndpoint,
    ChangeEvent,
    Delivery,
    Monitor,
    MonitorTag,
    Outbox,
    RuleEvaluation,
    RuleRevision,
    RuleState,
    Tag,
)


@lru_cache(maxsize=128)
def compiled(source: str) -> CheckedProgram:
    return compile_rule(source)


class RedisHistory:
    """Exact window-count projection using only the existing GET/SET ACL.

    Current event/time and committed-history watermark identify a cache version.
    PostgreSQL rebuilds missing counts; Redis stores bounded aggregates instead of
    a list proportional to the monitor's history. The caller holds the monitor lock.
    """

    def __init__(self, session: AsyncSession, redis: Redis, event: ChangeEvent):
        self.session, self.redis, self.event = session, redis, event

    async def count(self, seconds: int) -> int:
        if type(seconds) is not int or not 0 < seconds <= 604800:
            raise ValueError("invalid history window")
        key = f"warden:eventids:rule-history:{self.event.monitor_id}"
        high = (
            await self.session.scalar(
                select(func.max(ChangeEvent.id)).where(
                    ChangeEvent.monitor_id == self.event.monitor_id,
                    ChangeEvent.id < self.event.id,
                )
            )
            or 0
        )
        anchor = self.event.detected_at.isoformat()
        raw = await self.redis.get(key)
        counts: dict[str, int] = {}
        if raw and len(raw) <= 65536:
            try:
                cached = json.loads(raw)
                values = cached["counts"]
                if (
                    type(cached["watermark"]) is int
                    and cached["watermark"] == high
                    and cached["anchor"] == anchor
                    and cached["event_id"] == self.event.id
                    and isinstance(values, dict)
                    and len(values) <= 2048
                    and all(
                        isinstance(k, str)
                        and k.isascii()
                        and k.isdigit()
                        and 0 < int(k) <= 604800
                        and type(v) is int
                        and 0 <= v <= 1_000_000_000
                        for k, v in values.items()
                    )
                ):
                    counts = values
            except (ValueError, TypeError, KeyError, OverflowError):
                pass
        window = str(seconds)
        if window in counts:
            return counts[window]
        total = (
            await self.session.scalar(
                select(func.count())
                .select_from(ChangeEvent)
                .where(
                    ChangeEvent.monitor_id == self.event.monitor_id,
                    ChangeEvent.id < self.event.id,
                    ChangeEvent.meaningful.is_(True),
                    ChangeEvent.detected_at >= self.event.detected_at - timedelta(seconds=seconds),
                    ChangeEvent.detected_at <= self.event.detected_at,
                )
            )
            or 0
        )
        counts[window] = total
        await self.redis.set(
            key,
            json.dumps(
                {"watermark": high, "anchor": anchor, "event_id": self.event.id, "counts": counts}
            ),
            ex=604800,
        )
        return total


async def evaluate_event(
    session: AsyncSession,
    redis: Redis,
    monitor: Monitor,
    event: ChangeEvent,
    context: dict[str, Value],
) -> None:
    revision = await session.scalar(
        select(RuleRevision).where(
            RuleRevision.monitor_id == monitor.id,
            RuleRevision.user_id == monitor.user_id,
            RuleRevision.enabled.is_(True),
        )
    )
    if revision is None:
        return
    state = await session.get(RuleState, revision.id)
    if state is None:
        state = RuleState(revision_id=revision.id)
        session.add(state)
    record = RuleEvaluation(event_id=event.id, revision_id=revision.id, decision="no_match")
    session.add(record)
    try:
        checked = compiled(revision.source)
        if state.next_allowed_at and event.detected_at < state.next_allowed_at:
            record.decision = "cooldown"
            return
        if not await evaluate(checked, context, RedisHistory(session, redis, event)):
            return
        endpoints: dict[int, AlertEndpoint] = {}
        for index, action in enumerate(checked.program.actions):
            if action.kind != "tag":
                endpoint = await session.scalar(
                    select(AlertEndpoint).where(
                        AlertEndpoint.user_id == monitor.user_id,
                        AlertEndpoint.name == action.target,
                        AlertEndpoint.kind == action.kind,
                        AlertEndpoint.enabled.is_(True),
                    )
                )
                if endpoint is None:
                    raise RuleError("endpoint", "Action destination unavailable", action.span)
                endpoints[index] = endpoint
        record.decision = "matched"
        state.next_allowed_at = event.detected_at + timedelta(seconds=checked.program.cooldown_s)
        state.last_event_id = event.id
        for index, action in enumerate(checked.program.actions):
            if action.kind == "tag":
                tag_id = await session.scalar(
                    insert(Tag)
                    .values(id=uuid.uuid4(), user_id=monitor.user_id, name=action.target)
                    .on_conflict_do_nothing(index_elements=["user_id", "name"])
                    .returning(Tag.id)
                )
                if tag_id is None:
                    tag_id = await session.scalar(
                        select(Tag.id).where(
                            Tag.user_id == monitor.user_id, Tag.name == action.target
                        )
                    )
                await session.execute(
                    insert(MonitorTag)
                    .values(monitor_id=monitor.id, tag_id=tag_id)
                    .on_conflict_do_nothing()
                )
                continue
            endpoint = endpoints[index]
            identity = uuid.uuid5(revision.id, f"{event.id}:{index}")
            outbox = Outbox(
                kind="alert." + action.kind,
                payload={"delivery_id": str(identity), "traceparent": carrier()},
                idempotency_key=f"alert:{identity}",
            )
            session.add(outbox)
            await session.flush()
            body = json.dumps(
                {
                    "schema_version": 1,
                    "delivery_id": str(identity),
                    "event_id": str(event.id),
                    "monitor_id": str(monitor.id),
                    "revision_id": str(revision.id),
                    "detected_at": event.detected_at.isoformat(),
                    "kind": event.kind,
                    "score": event.score,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            session.add(
                Delivery(
                    id=identity,
                    outbox_id=outbox.id,
                    event_id=event.id,
                    revision_id=revision.id,
                    user_id=monitor.user_id,
                    endpoint_id=endpoint.id,
                    action_index=index,
                    endpoint_version=endpoint.version,
                    kind=action.kind,
                    destination=endpoint.target,
                    body=body,
                    secret_ref=endpoint.secret_ref,
                    key_id=endpoint.key_id,
                    traceparent=carrier(),
                )
            )
        await session.execute(text("SELECT pg_notify('warden_alerts', '')"))
    except RuleError as exc:
        record.decision, record.diagnostic = "error", exc.as_dict()
