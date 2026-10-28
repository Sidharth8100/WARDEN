"""Reproducible evaluation harness; synthetic price-drop preference, not human labels."""

import asyncio
import json
import logging
import random
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx
import structlog

from chaos_web.main import app
from warden_compare.classify import classify, extract_features, load_classifier_config
from warden_compare.extract import extract_price
from warden_compare.normalize import normalize
from warden_core.learning import OnlineLogistic, metrics
from warden_db.learning import feature_values, global_prior


async def corpus(size: int, seed: int) -> list[tuple[dict[str, float], bool, bool]]:
    result = []
    weights, threshold = load_classifier_config()
    selection = {"price_css": ".price-tag", "price_currency": "USD"}
    generator = random.Random(seed)
    with patch("chaos_web.main.random", generator):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chaos-web"
        ) as client:
            for index in range(size):
                identity = f"learning-{seed}-{index}"
                await client.post(f"/__reset__/{identity}")
                old = normalize((await client.get(f"/pages/price/{identity}")).content)
                truth = (await client.post(f"/__mutate__/price/{identity}")).json()
                new = normalize((await client.get(f"/pages/price/{identity}")).content)
                before, after = extract_price(old, selection), extract_price(new, selection)
                assert before is not None and after is not None
                drop = (before - after) / before * 100
                features = extract_features(old, new)
                values = feature_values(
                    asdict(features), {"fields": {"price.drop_percent": str(drop)}}
                )
                # Explicit tenant utility: only a price DECREASE is useful. The
                # simulator's generic signal also includes increases; retain this
                # distinction rather than claiming generic static-v2 is deficient.
                useful = truth["kind"] == "signal" and after < before
                baseline = classify(features, weights, threshold).meaningful
                result.append((values, useful, baseline))
    return result


async def run() -> dict[str, object]:
    train, test = await corpus(1200, 4721), await corpus(600, 9137)
    labels = [row[1] for row in test]
    baseline = metrics([row[2] for row in test], labels)
    prior = global_prior()
    model = OnlineLogistic(dict(prior), dict(prior))
    points = []
    for size in (0, 25, 50, 100, 200, 400, 800, 1200):
        while model.samples < size:
            features, useful, _ = train[model.samples]
            model.update(features, useful)
        points.append(
            {"labels": size, **metrics([model.probability(x[0]) >= 0.5 for x in test], labels)}
        )
    assert points[-1]["f1"] > baseline["f1"] + 0.01
    return {
        "dataset": "chaos-web price archetype; independent seeded page IDs",
        "label_policy": "synthetic tenant utility: price decrease only; increases are noise",
        "human_labels": False,
        "train_seed": 4721,
        "test_seed": 9137,
        "training_examples": len(train),
        "heldout_examples": len(test),
        "heldout_positive": sum(labels),
        "static_v2": baseline,
        "points": points,
        "note": (
            "No held-out updates or hyperparameter search; this is personalization, "
            "not generalization proof on real sites."
        ),
    }


def svg(result: dict[str, object]) -> str:
    points = result["points"]
    baseline = result["static_v2"]["f1"]
    coordinates = " ".join(
        f"{70 + p['labels'] / 1200 * 690:.2f},{340 - p['f1'] * 270:.2f}" for p in points
    )
    horizontal = 340 - baseline * 270
    ticks = "".join(
        f'<line x1="70" y1="{340 - v * 270}" x2="760" y2="{340 - v * 270}" stroke="#d6dce5"/>'
        f'<text x="28" y="{345 - v * 270}" font-size="13">{v:.1f}</text>'
        for v in (0, 0.2, 0.4, 0.6, 0.8, 1)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="840" height="440" viewBox="0 0 840 440">'
        '<rect width="840" height="440" fill="white"/><g font-family="sans-serif" fill="#172237">'
        '<text x="35" y="30" font-size="20">Online learning: synthetic price-drop preference</text>'
        '<text x="35" y="52" font-size="13">600 independent held-out pages; F1, threshold 0.5; '
        "no held-out training"
        "</text>"
        f'{ticks}<line x1="70" y1="{horizontal}" x2="760" y2="{horizontal}" '
        'stroke="#b64d16" stroke-dasharray="7 5"/>'
        f'<polyline points="{coordinates}" fill="none" stroke="#1765b0" stroke-width="3"/>'
        '<text x="70" y="365">0</text><text x="730" y="365">1200</text>'
        '<text x="300" y="392">Number of training labels</text><text x="12" y="70">F1</text>'
        f'<text x="70" y="420" fill="#b64d16">Static v2 F1: {baseline:.3f}</text>'
        '<text x="310" y="420" fill="#1765b0">Blue: hand-written online logistic regression'
        "</text></g></svg>\n"
    )


if __name__ == "__main__":
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    measured = asyncio.run(run())
    Path("docs/reports/part-3-learning-curve.json").write_text(
        json.dumps(measured, indent=2) + "\n"
    )
    Path("docs/reports/part-3-learning-curve.svg").write_text(svg(measured))
    print(json.dumps({"static_v2": measured["static_v2"], "final": measured["points"][-1]}))
