"""Interval-censored Poisson MAP and a feasible bounded Lagrangian allocation."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    interval_seconds: float
    changed: bool


@dataclass(frozen=True)
class MonitorBudgetInput:
    monitor_id: str
    change_rate: float
    weight: float = 1.0


def estimate_change_rate(
    observations: list[Observation], *, prior_shape: float, prior_rate: float
) -> float:
    """Maximize sum(log(1-exp(-lambda*T))) / -lambda*T plus Gamma log prior.

    For changed observations derivative is T/expm1(lambda*T); its derivative
    is -T²*exp(-lambda*T)/(1-exp(-lambda*T))². Add (shape-1)/lambda-rate
    and -(shape-1)/lambda² from the prior. A bracket safeguards Newton steps.
    shape >= 1 keeps the posterior concave. A boundary mode returns zero.
    """
    if not math.isfinite(prior_shape) or prior_shape < 1:
        raise ValueError("prior_shape must be finite and >= 1")
    if not math.isfinite(prior_rate) or prior_rate <= 0:
        raise ValueError("prior_rate must be finite and positive")
    if any(not math.isfinite(o.interval_seconds) or o.interval_seconds <= 0 for o in observations):
        raise ValueError("observation intervals must be finite and positive")
    changed = [o.interval_seconds for o in observations if o.changed]
    exposure = prior_rate + sum(o.interval_seconds for o in observations if not o.changed)
    if not changed:
        return (prior_shape - 1) / exposure

    def derivatives(rate: float) -> tuple[float, float]:
        first = (prior_shape - 1) / rate - exposure
        second = -(prior_shape - 1) / rate**2
        for interval in changed:
            x = rate * interval
            decay = math.exp(-x)
            denominator = -math.expm1(-x)
            first += interval * decay / denominator
            second -= interval**2 * decay / denominator**2
        return first, second

    low = 0.0
    high = max(1.0 / exposure, (len(changed) + prior_shape) / exposure)
    while derivatives(high)[0] > 0:
        high *= 2
    rate = high / 2
    for _ in range(150):
        first, second = derivatives(rate)
        if first > 0:
            low = rate
        else:
            high = rate
        proposal = rate - first / second if second < 0 else (low + high) / 2
        if not low < proposal < high:
            proposal = (low + high) / 2
        if high - low <= 1e-12 * max(high, 1e-12):
            return high
        rate = proposal
    return rate


def solve_crawl_intervals(
    monitors: list[MonitorBudgetInput],
    *,
    total_budget_per_second: float,
    min_interval_s: float,
    max_interval_s: float,
) -> dict[str, float]:
    """Minimize sum(w*lambda*I/2) with sum(1/I)<=B and explicit bounds.

    Stationarity gives I=sqrt(2*mu/(w*lambda)). Apply clamps INSIDE every
    candidate budget calculation (the KKT bounded optimum), not only afterward.
    Reject infeasibility rather than silently overspending. Zero-value monitors
    use max_interval; unused capacity need not be spent. Binary search returns
    the feasible side. This loss is a small-lambda*I approximation, not an exact
    freshness model for arbitrary rates; the simulator also measures exact age.
    """
    values = [total_budget_per_second, min_interval_s, max_interval_s]
    if any(not math.isfinite(v) or v <= 0 for v in values) or min_interval_s > max_interval_s:
        raise ValueError("finite positive budget and ordered positive bounds required")
    if len({m.monitor_id for m in monitors}) != len(monitors):
        raise ValueError("duplicate monitor ID")
    if any(
        not math.isfinite(m.change_rate * m.weight) or m.change_rate < 0 or m.weight <= 0
        for m in monitors
    ):
        raise ValueError("nonnegative finite rates and positive finite weights required")
    if not monitors:
        return {}
    if total_budget_per_second < len(monitors) / max_interval_s:
        raise ValueError("budget cannot fund every monitor at its maximum interval")

    def intervals(mu: float) -> dict[str, float]:
        return {
            m.monitor_id: max_interval_s
            if m.change_rate == 0
            else min(
                max_interval_s, max(min_interval_s, math.sqrt(2 * mu / (m.weight * m.change_rate)))
            )
            for m in monitors
        }

    result = intervals(0)
    if sum(1 / interval for interval in result.values()) <= total_budget_per_second:
        return result
    low = 0.0
    high = max(m.weight * m.change_rate for m in monitors) * max_interval_s**2 / 2
    for _ in range(120):
        mid = (low + high) / 2
        candidate = intervals(mid)
        if sum(1 / interval for interval in candidate.values()) > total_budget_per_second:
            low = mid
        else:
            high, result = mid, candidate
    return result
