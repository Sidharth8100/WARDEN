"""Seeded Poisson histories reused across competing scheduling policies."""

import random


def poisson_trace(rates: list[float], duration: float, seed: int) -> list[list[float]]:
    generator = random.Random(seed)
    histories = []
    for rate in rates:
        points = []
        now = generator.expovariate(rate)
        while now < duration:
            points.append(now)
            now += generator.expovariate(rate)
        histories.append(points)
    return histories
