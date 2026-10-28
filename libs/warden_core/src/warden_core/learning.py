"""Sparse signed feature hashing and online logistic residuals around a prior."""

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

DIMENSIONS = 4096


def hashed_features(features: Mapping[str, float]) -> dict[int, float]:
    if len(features) > 256:
        raise ValueError("feature count exceeded")
    result = {0: 1.0}
    for name, value in features.items():
        if not name or len(name) > 128 or not math.isfinite(value):
            raise ValueError("invalid feature")
        digest = hashlib.blake2b(name.encode(), digest_size=8).digest()
        index = 1 + int.from_bytes(digest[:6], "big") % (DIMENSIONS - 1)
        sign = 1 if digest[7] & 1 else -1
        result[index] = result.get(index, 0) + sign * max(-10, min(10, value))
    return result


@dataclass
class OnlineLogistic:
    weights: dict[int, float] = field(default_factory=dict)
    prior: dict[int, float] = field(default_factory=dict)
    samples: int = 0

    def __post_init__(self) -> None:
        if self.samples < 0 or any(
            not 0 <= k < DIMENSIONS or not math.isfinite(v)
            for mapping in (self.weights, self.prior)
            for k, v in mapping.items()
        ):
            raise ValueError("invalid model state")

    def probability(self, features: Mapping[str, float]) -> float:
        value = sum(
            self.weights.get(k, self.prior.get(k, 0)) * x
            for k, x in hashed_features(features).items()
        )
        if value >= 0:
            return 1 / (1 + math.exp(-value))
        positive = math.exp(value)
        return positive / (1 + positive)

    def update(
        self, features: Mapping[str, float], label: bool, *, rate: float = 0.2, l2: float = 0.001
    ) -> float:
        if type(label) is not bool or not 0 < rate <= 1 or not 0 <= l2 <= 1:
            raise ValueError("invalid SGD parameters")
        prediction = self.probability(features)
        step = rate / math.sqrt(1 + self.samples / 100)
        vector = hashed_features(features)
        for k in self.weights.keys() | self.prior.keys() | vector.keys():
            old = self.weights.get(k, self.prior.get(k, 0))
            penalty = l2 * (old - self.prior.get(k, 0)) if k else 0
            self.weights[k] = old - step * ((prediction - label) * vector.get(k, 0) + penalty)
        self.samples += 1
        return prediction

    def serialized(self) -> dict[str, float]:
        return {str(k): v for k, v in sorted(self.weights.items())}


def metrics(predictions: Sequence[bool], labels: Sequence[bool]) -> dict[str, float]:
    if len(predictions) != len(labels) or not labels:
        raise ValueError("nonempty aligned evaluation required")
    tp = sum(a and b for a, b in zip(predictions, labels, strict=True))
    fp = sum(a and not b for a, b in zip(predictions, labels, strict=True))
    fn = sum(not a and b for a, b in zip(predictions, labels, strict=True))
    return {
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
        "precision": tp / (tp + fp) if tp + fp else 0,
        "recall": tp / (tp + fn) if tp + fn else 0,
    }


def promotion_allowed(
    candidate: Mapping[str, float],
    incumbent: Mapping[str, float],
    labels: Sequence[bool],
    *,
    margin: float = 0.01,
) -> bool:
    values = [candidate.get("f1", -1), candidate.get("precision", -1), incumbent.get("f1", -1)]
    return (
        len(labels) >= 20
        and any(labels)
        and not all(labels)
        and all(math.isfinite(v) and 0 <= v <= 1 for v in values)
        and math.isfinite(margin)
        and margin >= 0
        and values[0] >= values[2] + margin
        and values[1] >= 0.8
    )


def held_out(event_id: int, user_id: str) -> bool:
    digest = hashlib.blake2b(f"{user_id}:{event_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % 5 == 0
