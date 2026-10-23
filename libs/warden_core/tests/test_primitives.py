"""Independent references for membership, timers, estimation, and bounded routing."""

import heapq
import math
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from warden_core.bloomfilter import BloomFilter, CountingBloomFilter
from warden_core.budget import (
    MonitorBudgetInput,
    Observation,
    estimate_change_rate,
    solve_crawl_intervals,
)
from warden_core.hashring import ConsistentHashRing
from warden_core.timingwheel import TimingWheel


@given(st.sets(st.binary(max_size=20), min_size=1, max_size=100))
def test_bloom_never_forgets(items: set[bytes]) -> None:
    bloom = BloomFilter(len(items), 0.01)
    for item in items:
        bloom.add(item)
    assert all(item in bloom for item in items)


def test_bloom_false_positive_band_and_counting_overflow() -> None:
    bloom = BloomFilter(10_000, 0.01)
    for i in range(10_000):
        bloom.add(f"added-{i}".encode())
    fraction = sum(f"absent-{i}".encode() in bloom for i in range(50_000)) / 50_000
    assert 0.005 < fraction < 0.018  # fixed disjoint corpus; generous sampling band
    counting = CountingBloomFilter(100, 0.01)
    counting.add(b"hello")
    counting.remove(b"hello")
    assert b"hello" not in counting
    with pytest.raises(ValueError):
        counting.remove(b"hello")
    for _ in range(255):
        counting.add(b"hello")
    with pytest.raises(OverflowError):
        counting.add(b"hello")
    for _ in range(255):
        counting.remove(b"hello")
    assert b"hello" not in counting


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 2), st.integers(0, 3000)), max_size=100))
def test_timing_wheel_matches_independent_heap(operations: list[tuple[int, int]]) -> None:
    wheel = TimingWheel(10, [4, 4, 4])
    heap: list[tuple[int, int]] = []
    cancelled: set[int] = set()
    handles = []
    now = 0
    for operation, value in operations:
        if operation == 0:
            identity = len(handles)
            handles.append(wheel.schedule(value, str(identity)))
            deadline = now if value == 0 else ((now + value + 9) // 10) * 10
            heapq.heappush(heap, (deadline, identity))
        elif operation == 1 and handles:
            identity = value % len(handles)
            if wheel.cancel(handles[identity]):
                cancelled.add(identity)
        else:
            now += value
            expected = []
            while heap and heap[0][0] <= now:
                _, identity = heapq.heappop(heap)
                if identity not in cancelled:
                    expected.append(str(identity))
            assert Counter(wheel.advance(now)) == Counter(expected)
    expected = [str(identity) for _, identity in heap if identity not in cancelled]
    assert Counter(wheel.advance(now + 5000)) == Counter(expected)


def test_timer_zero_cancel_foreign_and_backwards() -> None:
    wheel = TimingWheel(10, [4])
    handle = wheel.schedule(0, "ready")
    assert not TimingWheel(10, [4]).cancel(handle)
    assert wheel.advance(0) == ["ready"]
    assert not wheel.cancel(handle)
    with pytest.raises(ValueError):
        wheel.advance(-1)


def test_poisson_map_against_direct_likelihood() -> None:
    # Equal intervals permit an independent exact MLE: -log(fraction unchanged)/T.
    observations = [Observation(20, i < 4000) for i in range(10_000)]
    expected = -math.log(0.6) / 20
    estimate = estimate_change_rate(observations, prior_shape=1, prior_rate=0.001)
    assert estimate == pytest.approx(expected, rel=1e-5)

    def log_posterior(rate: float) -> float:
        return (
            sum(
                math.log(-math.expm1(-rate * o.interval_seconds))
                if o.changed
                else -rate * o.interval_seconds
                for o in observations
            )
            - 0.001 * rate
        )

    assert log_posterior(estimate) > log_posterior(estimate * 1.01)
    assert log_posterior(estimate) > log_posterior(estimate * 0.99)
    assert estimate_change_rate([], prior_shape=2, prior_rate=100) == 0.01
    assert estimate_change_rate([], prior_shape=1, prior_rate=100) == 0


@given(st.lists(st.floats(min_value=0, max_value=10, allow_nan=False), min_size=1, max_size=20))
def test_budget_is_feasible_after_clamping(rates: list[float]) -> None:
    monitors = [MonitorBudgetInput(str(i), rate) for i, rate in enumerate(rates)]
    budget = len(rates) / 30
    intervals = solve_crawl_intervals(
        monitors, total_budget_per_second=budget, min_interval_s=1, max_interval_s=100
    )
    assert sum(1 / interval for interval in intervals.values()) <= budget + 1e-12
    assert all(1 <= interval <= 100 for interval in intervals.values())


def test_budget_order_bounds_and_infeasibility() -> None:
    monitors = [MonitorBudgetInput("fast", 1), MonitorBudgetInput("slow", 0.01)]
    result = solve_crawl_intervals(
        monitors, total_budget_per_second=0.2, min_interval_s=1, max_interval_s=100
    )
    assert result["fast"] < result["slow"]
    with pytest.raises(ValueError, match="cannot fund"):
        solve_crawl_intervals(
            monitors, total_budget_per_second=0.001, min_interval_s=1, max_interval_s=100
        )


@given(st.text())
def test_ring_determinism_and_load_accounting(key: str) -> None:
    ring = ConsistentHashRing(64)
    for member in ("a", "b", "c"):
        ring.add_member(member)
    assert ring.get_member(key) == ring.get_member(key)
    selected = ring.get_member(key)
    ring.report_load(selected, 1)
    ring.report_load(selected, -1)
    with pytest.raises(ValueError):
        ring.report_load(selected, -1)


def test_bounded_loads_cap_against_brute_arithmetic() -> None:
    ring = ConsistentHashRing(128, 0.25)
    members = ["a", "b", "c", "d"]
    for member in members:
        ring.add_member(member)
    loads: Counter[str] = Counter()
    for i in range(5000):
        member = ring.get_member(f"key-{i}")
        loads[member] += 1
        ring.report_load(member, 1)
        assert loads[member] <= math.ceil(1.25 * (i + 1) / len(members))
    assert max(loads.values()) / (5000 / len(members)) <= 1.251
