"""Transactional per-account SGD, immutable registry versions and held-out gates."""

import math
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from warden_core.learning import (
    OnlineLogistic,
    hashed_features,
    held_out,
    metrics,
    promotion_allowed,
)
from warden_db.models import (
    ChangeEvent,
    FeedbackLabel,
    LearningExample,
    ModelAlias,
    ModelVersion,
    ShadowScore,
)


def feature_values(
    features: Mapping[str, object], extracted: Mapping[str, object] | None
) -> dict[str, float]:
    result: dict[str, float] = {}
    for name, value in features.items():
        if isinstance(value, (int, float)) and math.isfinite(value):
            scale = (
                64
                if name == "simhash_distance"
                else 100
                if name == "visual_bbox_count"
                else 10
                if name == "date_timestamp_hit_count"
                else 1
            )
            result[name] = max(-10, min(10, float(value) / scale))
    fields = extracted.get("fields") if extracted else None
    if isinstance(fields, dict):
        raw = fields.get("price.drop_percent")
        if isinstance(raw, str):
            try:
                value = float(raw)
                if math.isfinite(value):
                    result["price.drop_percent"] = max(-1, min(1, value / 100))
                    result["price.is_drop"] = float(value > 0)
            except ValueError:
                pass
    return result


def global_prior() -> dict[int, float]:
    coefficients = {
        "text_change_ratio": 2.7,
        "is_numeric_only": 2.1,
        "date_timestamp_hit_count": -2.4,
        "dom_depth_score": 0.9,
        "is_ad_or_banner": -2.4,
        "has_content_structure_change": 2.1,
        "is_timestamp_only": -3.0,
    }
    weights = {0: -2.1}
    for name, coefficient in coefficients.items():
        for index, value in hashed_features({name: 1}).items():
            if index:
                weights[index] = weights.get(index, 0) + coefficient * value
    return weights


def load_model(version: ModelVersion) -> OnlineLogistic:
    return OnlineLogistic(
        {int(k): v for k, v in version.weights.items()},
        {int(k): v for k, v in version.prior_weights.items()},
        version.samples,
    )


async def alias(session: AsyncSession, scope: str, *, lock: bool = False) -> ModelAlias:
    await session.execute(insert(ModelAlias).values(scope=scope).on_conflict_do_nothing())
    row = await session.get(ModelAlias, scope, with_for_update=lock, populate_existing=lock)
    if row is None:
        raise RuntimeError("model alias missing")
    return row


async def ensure_global_prior(session: AsyncSession) -> ModelVersion:
    """Persist a reproducible seed without silently replacing the static incumbent."""
    pointer = await alias(session, "global", lock=True)
    identity = pointer.production_id or pointer.shadow_id
    if identity:
        existing = await session.get(ModelVersion, identity)
        if existing is not None:
            return existing
    prior = global_prior()
    version = ModelVersion(
        user_id=None,
        weights={str(k): v for k, v in prior.items()},
        prior_weights={str(k): v for k, v in prior.items()},
        samples=0,
        feature_schema="hashed-v1",
        metrics={"origin": "reviewed static-v2-derived seed; not trained on tenant labels"},
    )
    session.add(version)
    await session.flush()
    pointer.shadow_id = version.id
    return version


async def add_feedback(
    session: AsyncSession, event: ChangeEvent, user_id: uuid.UUID, useful: bool
) -> ModelVersion | None:
    """Caller verifies event ownership. Labels are immutable/idempotent, no holdout SGD."""
    pointer = await alias(session, str(user_id), lock=True)
    existing = await session.get(LearningExample, (event.id, user_id))
    if existing:
        if existing.label != useful:
            raise ValueError("feedback_already_recorded")
        return await session.get(ModelVersion, pointer.shadow_id) if pointer.shadow_id else None
    features = feature_values(event.features or {}, event.extracted)
    is_holdout = held_out(event.id, str(user_id))
    sample = LearningExample(
        event_id=event.id,
        user_id=user_id,
        label=useful,
        features=features,
        split="holdout" if is_holdout else "train",
        static_prediction=bool((event.features or {}).get("static_prediction", event.meaningful)),
    )
    session.add(sample)
    session.add(
        FeedbackLabel(
            event_id=event.id,
            user_id=user_id,
            label="useful" if useful else "noise",
            feature_snapshot=features,
        )
    )
    if is_holdout:
        return None
    current = await session.get(ModelVersion, pointer.shadow_id) if pointer.shadow_id else None
    if current:
        model = load_model(current)
    else:
        prior_version = await ensure_global_prior(session)
        prior = load_model(prior_version).weights
        model = OnlineLogistic(dict(prior), dict(prior))
    model.update(features, useful)
    version = ModelVersion(
        user_id=user_id,
        parent_id=current.id if current else None,
        weights=model.serialized(),
        prior_weights={str(k): v for k, v in model.prior.items()},
        samples=model.samples,
        feature_schema="hashed-v1",
    )
    session.add(version)
    await session.flush()
    pointer.shadow_id = version.id
    sample.trained_at = datetime.now(UTC)
    return version


async def score_observation(session: AsyncSession, event: ChangeEvent, user_id: uuid.UUID) -> None:
    features = feature_values(event.features or {}, event.extracted)
    pointer = await session.get(ModelAlias, str(user_id))
    global_pointer = await session.get(ModelAlias, "global")
    production_id = (
        pointer.production_id
        if pointer and pointer.production_id
        else global_pointer.production_id
        if global_pointer
        else None
    )
    if production_id:
        version = await session.get(ModelVersion, production_id)
        if version:
            probability = load_model(version).probability(features)
            event.meaningful = probability >= 0.5
            event.score = probability
            event.classifier_version = "online:" + str(version.id)
    if pointer and pointer.shadow_id:
        candidate = await session.get(ModelVersion, pointer.shadow_id)
        if candidate:
            probability = load_model(candidate).probability(features)
            session.add(
                ShadowScore(
                    event_id=event.id,
                    model_id=candidate.id,
                    probability=probability,
                    shadow_prediction=probability >= 0.5,
                    production_prediction=event.meaningful,
                )
            )


async def promote(session: AsyncSession, user_id: uuid.UUID) -> dict[str, object]:
    pointer = await alias(session, str(user_id), lock=True)
    candidate = await session.get(ModelVersion, pointer.shadow_id) if pointer.shadow_id else None
    if candidate is None or candidate.user_id != user_id:
        raise ValueError("no_owned_shadow_model")
    examples = (
        await session.scalars(
            select(LearningExample)
            .where(LearningExample.user_id == user_id, LearningExample.split == "holdout")
            .order_by(LearningExample.event_id)
        )
    ).all()
    if not examples:
        raise ValueError("insufficient_holdout")
    labels = [x.label for x in examples]
    predictions = [load_model(candidate).probability(x.features) >= 0.5 for x in examples]
    incumbent = (
        await session.get(ModelVersion, pointer.production_id) if pointer.production_id else None
    )
    baseline = [
        load_model(incumbent).probability(x.features) >= 0.5 if incumbent else x.static_prediction
        for x in examples
    ]
    candidate_metrics, incumbent_metrics = metrics(predictions, labels), metrics(baseline, labels)
    allowed = promotion_allowed(candidate_metrics, incumbent_metrics, labels)
    result: dict[str, object] = {
        "allowed": allowed,
        "candidate": candidate_metrics,
        "incumbent": incumbent_metrics,
        "holdout_event_ids": [x.event_id for x in examples],
        "candidate_id": str(candidate.id),
    }
    if not allowed:
        return result
    pointer.previous_id, pointer.production_id = pointer.production_id, candidate.id
    return result


async def rollback(session: AsyncSession, user_id: uuid.UUID) -> None:
    pointer = await alias(session, str(user_id), lock=True)
    if pointer.production_id is None:
        raise ValueError("no_production_model")
    pointer.production_id, pointer.previous_id = pointer.previous_id, pointer.production_id
