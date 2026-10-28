"""Numerics and independent promotion invariants, including worse-candidate rejection."""

import math

import pytest

from warden_core.learning import (
    OnlineLogistic,
    hashed_features,
    held_out,
    metrics,
    promotion_allowed,
)


def test_sgd_learns_and_tenant_residuals_do_not_mutate_global_prior() -> None:
    prior = {0: -0.2}
    model = OnlineLogistic(dict(prior), dict(prior))
    for _ in range(200):
        model.update({"drop": 1}, True)
        model.update({"drop": -1}, False)
    assert model.probability({"drop": 1}) > 0.9
    assert model.probability({"drop": -1}) < 0.1
    assert prior == {0: -0.2} and model.prior == prior
    assert OnlineLogistic({0: -1000}).probability({}) == 0
    assert OnlineLogistic({0: 1000}).probability({}) == 1
    assert hashed_features({"abc": 1}) == hashed_features({"abc": 1})
    assert all(0 <= index < 4096 for index in hashed_features({"abc": 1}))


@pytest.mark.parametrize(
    "candidate",
    [
        {"f1": 0.7, "precision": 0.9},
        {"f1": 0.8, "precision": 0.9},
        {"f1": 0.99, "precision": 0.7},
        {"f1": math.nan, "precision": 0.9},
    ],
)
def test_worse_equal_low_precision_or_nonfinite_models_never_promote(
    candidate: dict[str, float],
) -> None:
    assert not promotion_allowed(candidate, {"f1": 0.8}, [True, False] * 20)


def test_holdout_gate_and_metric_reference() -> None:
    assert metrics([True, True, False, False], [True, False, True, False])["f1"] == 0.5
    assert promotion_allowed({"f1": 0.9, "precision": 0.9}, {"f1": 0.8}, [True, False] * 20)
    assert not promotion_allowed({"f1": 0.9, "precision": 0.9}, {"f1": 0.8}, [True] * 40)
    assert not promotion_allowed({"f1": 0.9, "precision": 0.9}, {"f1": 0.8}, [True, False])
    assert held_out(100, "tenant") == held_out(100, "tenant")
    with pytest.raises(ValueError):
        OnlineLogistic().update({"x": math.inf}, True)
