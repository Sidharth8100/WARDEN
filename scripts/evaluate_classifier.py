"""Gate 1 regression evaluation against simulator and labeled real pairs.

Evaluates change classifier performance against ground-truth simulator mutations
from chaos-web across 200+ trials, along with any labeled real-world web pairs.
"""

import argparse
import asyncio
import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from httpx import ASGITransport

from chaos_web.main import app as chaos_app
from warden_compare.classify import (
    CLASSIFIER_VERSION,
    classify,
    extract_features,
    load_classifier_config,
)
from warden_compare.normalize import normalize

LABELS_FILE = Path("tests/fixtures/real_pairs/labels.json")


@dataclass
class ConfusionMatrix:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.total if self.total > 0 else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


async def evaluate_sim_pairs(
    trials_per_archetype: int = 70,
) -> tuple[ConfusionMatrix, dict[str, dict[str, int]]]:
    """Generate and evaluate 200+ pairs against chaos-web with authoritative ground truth."""
    transport = ASGITransport(app=chaos_app)
    weights, threshold = load_classifier_config()
    cm = ConfusionMatrix()
    type_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})

    async with httpx.AsyncClient(transport=transport, base_url="http://chaos-web") as client:
        archetypes = ["price", "jobs", "app"]

        for arch in archetypes:
            for i in range(trials_per_archetype):
                page_id = f"eval-{arch}-{i}"
                await client.post(f"/__reset__/{page_id}")

                # 1. Fetch before HTML
                r_before = await client.get(f"/pages/{arch}/{page_id}")
                html_before = r_before.text.encode("utf-8")
                # Archetype 3 (app) represents a JS-rendered SPA (Tier-2 monitor).
                # Extract rendered client-side DOM if evaluating SPA archetype.
                if arch == "app":
                    import re

                    match_b = re.search(r"root\.innerHTML\s*=\s*'([^']+)';", r_before.text)
                    if match_b:
                        inner_b = match_b.group(1)
                        rendered_b = f"<html><body><div id='root'>{inner_b}</div></body></html>"
                        html_before = rendered_b.encode()

                # 2. Trigger mutation
                mut_res = await client.post(f"/__mutate__/{arch}/{page_id}")
                mut_data = mut_res.json()
                ground_truth_kind = mut_data["kind"]  # "signal" | "noise"
                desc = mut_data["description"]
                is_signal = ground_truth_kind == "signal"

                # 3. Fetch after HTML
                r_after = await client.get(f"/pages/{arch}/{page_id}")
                html_after = r_after.text.encode("utf-8")
                if arch == "app":
                    match_a = re.search(r"root\.innerHTML\s*=\s*'([^']+)';", r_after.text)
                    if match_a:
                        inner_a = match_a.group(1)
                        rendered_a = f"<html><body><div id='root'>{inner_a}</div></body></html>"
                        html_after = rendered_a.encode()

                # 4. Normalize and Classify
                doc_a = normalize(html_before)
                doc_b = normalize(html_after)
                features = extract_features(doc_a, doc_b)
                result = classify(features, weights, threshold)

                pred_meaningful = result.meaningful

                # Update confusion matrix
                if is_signal and pred_meaningful:
                    cm.tp += 1
                elif not is_signal and pred_meaningful:
                    cm.fp += 1
                elif not is_signal and not pred_meaningful:
                    cm.tn += 1
                else:
                    cm.fn += 1

                # Update per-mutation category stats
                category_key = f"{arch}: {desc.split(':')[0]}"
                type_stats[category_key]["total"] += 1
                if is_signal == pred_meaningful:
                    type_stats[category_key]["correct"] += 1

    return cm, type_stats


def evaluate_real_pairs() -> ConfusionMatrix | None:
    """Evaluate classifier against human-labeled real website snapshot pairs."""
    if not LABELS_FILE.is_file():
        return None

    try:
        labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not labels:
        return None

    weights, threshold = load_classifier_config()
    cm = ConfusionMatrix()

    for item in labels:
        f_a = Path(item["file_a"])
        f_b = Path(item["file_b"])
        if not f_a.is_file() or not f_b.is_file():
            continue

        doc_a = normalize(f_a.read_bytes())
        doc_b = normalize(f_b.read_bytes())
        features = extract_features(doc_a, doc_b)
        res = classify(features, weights, threshold)

        true_meaningful = bool(item["meaningful"])
        pred_meaningful = res.meaningful

        if true_meaningful and pred_meaningful:
            cm.tp += 1
        elif not true_meaningful and pred_meaningful:
            cm.fp += 1
        elif not true_meaningful and not pred_meaningful:
            cm.tn += 1
        else:
            cm.fn += 1

    return cm if cm.total > 0 else None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=4721)
    parser.add_argument("--trials-per-archetype", type=int, default=70)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.trials_per_archetype < 1:
        parser.error("trials-per-archetype must be positive")
    # Only the local simulator uses this PRNG. No credentials are generated here.
    random.seed(args.seed)
    print("=" * 70)
    print(f"WARDEN GATE 1 CLASSIFIER {CLASSIFIER_VERSION} EVALUATION REPORT (seed {args.seed})")
    print("=" * 70)

    # 1. Sim pairs evaluation
    print(
        f"\n[+] Evaluating {args.trials_per_archetype * 3} synthetic pairs "
        "against chaos-web ground truth..."
    )
    sim_cm, type_stats = await evaluate_sim_pairs(trials_per_archetype=args.trials_per_archetype)

    print("\n--- SIMULATION RESULTS (Synthetic Internet) ---")
    print(f"Total Sim Pairs Evaluated: {sim_cm.total}")
    print(f"Overall Sim Accuracy:      {sim_cm.accuracy * 100:.2f}%")
    print(f"Precision:                 {sim_cm.precision * 100:.2f}%")
    print(f"Recall:                    {sim_cm.recall * 100:.2f}%")
    print(f"F1 Score:                  {sim_cm.f1:.4f}")
    print("\nConfusion Matrix:")
    print(f"  True Positives (TP):  {sim_cm.tp:<5} | False Positives (FP): {sim_cm.fp}")
    print(f"  False Negatives (FN): {sim_cm.fn:<5} | True Negatives (TN):  {sim_cm.tn}")

    print("\nPer-Mutation Breakdown:")
    for cat, stats in sorted(type_stats.items()):
        total = stats["total"]
        corr = stats["correct"]
        acc = (corr / total * 100) if total > 0 else 0.0
        print(f"  - {cat:<40}: {corr}/{total} ({acc:.1f}%)")

    # 2. Real pairs evaluation
    print("\n--- REAL PAIRS RESULTS (Captured Targets) ---")
    real_cm = evaluate_real_pairs()
    if real_cm is not None:
        print(f"Total Real Pairs Evaluated: {real_cm.total}")
        print(f"Overall Real Accuracy:     {real_cm.accuracy * 100:.2f}%")
        print(f"Precision:                 {real_cm.precision * 100:.2f}%")
        print(f"Recall:                    {real_cm.recall * 100:.2f}%")
        print(f"F1 Score:                  {real_cm.f1:.4f}")
        print("\nConfusion Matrix:")
        print(f"  True Positives (TP):  {real_cm.tp:<5} | False Positives (FP): {real_cm.fp}")
        print(f"  False Negatives (FN): {real_cm.fn:<5} | True Negatives (TN):  {real_cm.tn}")
    else:
        print("No human-labeled real pairs recorded yet in tests/fixtures/real_pairs/labels.json.")
        print("Human operator must run `make capture-pair URL=...` and label snapshots.")

    print("\n" + "=" * 70)
    if args.output:
        args.output.write_text(
            json.dumps(
                {
                    "classifier_version": CLASSIFIER_VERSION,
                    "seed": args.seed,
                    "simulation": {
                        **asdict(sim_cm),
                        "pairs": sim_cm.total,
                        "precision": sim_cm.precision,
                        "recall": sim_cm.recall,
                        "f1": sim_cm.f1,
                        "accuracy": sim_cm.accuracy,
                    },
                    "mutation_categories": dict(type_stats),
                    "real_pairs": asdict(real_cm) if real_cm else None,
                    "scope": (
                        "synthetic regression suite used during development; "
                        "not held-out real-world validation"
                    ),
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
