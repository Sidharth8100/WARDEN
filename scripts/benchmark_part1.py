"""Reproducible pure-algorithm measurements; no network, credentials, or Docker."""

import argparse
import json
import math
import platform
import time
from collections import Counter
from pathlib import Path

from chaos_web.benchmarks import poisson_trace
from warden_core.budget import MonitorBudgetInput, solve_crawl_intervals
from warden_core.hashring import ConsistentHashRing
from warden_core.timingwheel import TimingWheel


def timing(count: int) -> dict[str, float | int]:
    wheel = TimingWheel(10, [64, 64, 64])
    start = time.perf_counter()
    for index in range(count):
        wheel.schedule((index * 15485863 % 100_000 + 1) * 10, str(index))
    inserted = time.perf_counter()
    callbacks = wheel.advance(1_000_000)
    finished = time.perf_counter()
    if len(callbacks) != count or len(set(callbacks)) != count:
        raise RuntimeError("timer loss/duplicate in benchmark")
    return {
        "timers": count,
        "insert_s": inserted - start,
        "fire_s": finished - inserted,
        "operations_per_second": 2 * count / (finished - start),
    }


def ring_measurements(count: int) -> dict[str, float | int | str]:
    members = [f"worker-{index}" for index in range(8)]
    ring = ConsistentHashRing(512, math.inf)
    for member in members:
        ring.add_member(member)
    keys = [f"domain-{index}.example" for index in range(count)]
    original = [ring.get_member(key) for key in keys]
    ring.add_member("new-worker")
    after_add = [ring.get_member(key) for key in keys]
    ring.remove_member("new-worker")
    ring.remove_member(members[0])
    after_remove = [ring.get_member(key) for key in keys]
    workloads = ["hot-domain.example"] * (count // 5) + keys[count // 5 :]
    imbalance = {}
    for label, epsilon in [("ordinary", math.inf), ("bounded", 0.25)]:
        instance = ConsistentHashRing(512, epsilon)
        for member in members:
            instance.add_member(member)
        loads: Counter[str] = Counter()
        for key in workloads:
            member = instance.get_member(key)
            loads[member] += 1
            instance.report_load(member, 1)
        imbalance[label] = max(loads.values()) / (count / len(members))
    return {
        "keys": count,
        "members": len(members),
        "addition_remap": sum(a != b for a, b in zip(original, after_add, strict=True)) / count,
        "addition_theory": 1 / 9,
        "removal_remap": sum(a != b for a, b in zip(original, after_remove, strict=True)) / count,
        "removal_theory": 1 / 8,
        "load_workload": "20% one hot domain, 80% distinct domains, reported per assignment",
        "ordinary_max_over_mean": imbalance["ordinary"],
        "bounded_max_over_mean": imbalance["bounded"],
    }


def budget_measurements() -> dict[str, object]:
    rates = [0.00002, 0.00008, 0.00032, 0.00128]
    budget, duration = 0.1, 864_000.0
    inputs = [MonitorBudgetInput(str(index), rate) for index, rate in enumerate(rates)]
    optimum = solve_crawl_intervals(
        inputs, total_budget_per_second=budget, min_interval_s=10, max_interval_s=1000
    )
    fixed = {str(index): len(rates) / budget for index in range(len(rates))}
    # Independent proportional baseline: allocate polling frequencies in
    # proportion to estimated changes, respecting the same bounds and budget.
    low, high = 0.0, 1.0

    def proportional(factor: float) -> dict[str, float]:
        return {str(i): min(1000, max(10, factor / rate)) for i, rate in enumerate(rates)}

    while sum(1 / value for value in proportional(high).values()) > budget:
        high *= 2
    for _ in range(100):
        mid = (low + high) / 2
        if sum(1 / value for value in proportional(mid).values()) > budget:
            low = mid
        else:
            high = mid
    changes = poisson_trace(rates, duration, 4721)
    output = {}
    for name, intervals in [
        ("fixed", fixed),
        ("proportional", proportional(high)),
        ("optimized", optimum),
    ]:
        stale = 0.0
        checks = 0
        for index, points in enumerate(changes):
            interval = intervals[str(index)]
            last = 0.0
            point = 0
            while last < duration:
                end = min(duration, last + interval)
                while point < len(points) and points[point] <= last:
                    point += 1
                if point < len(points) and points[point] < end:
                    stale += end - points[point]
                if end < duration:
                    checks += 1
                last = end
        theoretical = sum(
            1 - (-math.expm1(-rate * intervals[str(index)])) / (rate * intervals[str(index)])
            for index, rate in enumerate(rates)
        ) / len(rates)
        output[name] = {
            "intervals_s": intervals,
            "checks": checks,
            "scheduled_rate": sum(1 / value for value in intervals.values()),
            "simulated_stale_fraction": stale / (duration * len(rates)),
            "theoretical_stale_fraction": theoretical,
        }
    return {
        "seed": 4721,
        "duration_s": duration,
        "budget_per_s": budget,
        "model": "independent Poisson changes; stale from first unseen change until poll",
        "baselines": output,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timers", type=int, default=1_000_000)
    parser.add_argument("--keys", type=int, default=100_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.timers < 1 or args.keys < 10:
        parser.error("positive timer count and at least 10 keys required")
    result = {
        "python": platform.python_version(),
        "timing_wheel": timing(args.timers),
        "hash_ring": ring_measurements(args.keys),
        "crawl_budget": budget_measurements(),
    }
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
