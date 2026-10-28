"""Versioned context serialization, never arbitrary JSON-to-object evaluation."""

from collections.abc import Mapping
from decimal import Decimal

from warden_rules.evaluator import Value
from warden_rules.language import FIELDS


def build_context(
    *,
    kind: str,
    score: float | None,
    meaningful: bool,
    before: str,
    after: str,
    previous_price: Decimal | None,
    current_price: Decimal | None,
    currency: str | None,
) -> dict[str, Value]:
    drop = (
        100 * (previous_price - current_price) / previous_price
        if previous_price is not None and previous_price > 0 and current_price is not None
        else None
    )
    return {
        "event.kind": kind,
        "event.score": Decimal(str(score)) if score is not None else None,
        "event.meaningful": meaningful,
        "text.before": before,
        "text.after": after,
        "price.previous": previous_price,
        "price.current": current_price,
        "price.drop_percent": drop,
        "price.currency": currency,
    }


def serialize(context: Mapping[str, Value]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "fields": {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in context.items()
        },
    }


def deserialize(payload: Mapping[str, object]) -> dict[str, Value]:
    fields = payload.get("fields")
    if payload.get("schema_version") != 1 or not isinstance(fields, dict):
        raise ValueError("unsupported rule context")
    context: dict[str, Value] = {}
    for key, value in fields.items():
        if not isinstance(key, str) or key not in FIELDS:
            raise ValueError("unknown context field")
        if value is None or type(value) is bool:
            context[key] = value
        elif isinstance(value, str):
            context[key] = Decimal(value) if FIELDS[key] == "Decimal" else value
        else:
            raise ValueError("invalid context representation")
    return context
