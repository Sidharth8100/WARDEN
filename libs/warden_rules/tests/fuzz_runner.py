"""Seeded syntax fuzz proof; no exceptions are swallowed except RuleError."""

import argparse
import json
import random
import time
from pathlib import Path

from warden_rules.language import RuleError, parse, pretty


def run(count: int, seed: int) -> dict[str, object]:
    rng = random.Random(seed)
    started = time.monotonic()
    accepted = rejected = valid = 0

    def arithmetic(depth: int) -> str:
        if depth <= 0 or rng.random() < 0.4:
            return str(rng.randrange(100))
        return (
            f"({arithmetic(depth - 1)} {rng.choice(['+', '-', '*', '/'])} {arithmetic(depth - 1)})"
        )

    def boolean(depth: int) -> str:
        if depth <= 0 or rng.random() < 0.5:
            return (
                f"{arithmetic(2)} {rng.choice(['==', '!=', '<', '<=', '>', '>='])} {arithmetic(2)}"
            )
        return f"({boolean(depth - 1)} {rng.choice(['and', 'or'])} {boolean(depth - 1)})"

    for i in range(count):
        source = (
            f'when {boolean(3)} then tag("fuzz-{rng.randrange(100)}") '
            f"cooldown {rng.randrange(1, 100)}s"
        )
        generated_valid = i % 4 != 0
        if not generated_valid:
            pos = rng.randrange(len(source))
            source = source[:pos] + rng.choice(["@", '"', ")", "\\", "#", "\x00"]) + source[pos:]
        try:
            program = parse(source)
        except RuleError:
            if generated_valid:
                raise
            rejected += 1
            continue
        accepted += 1
        valid += generated_valid
        printed = pretty(program)
        restored = parse(printed)
        if restored.normalized() != program.normalized() or pretty(restored) != printed:
            raise AssertionError("round-trip mismatch")
    return {
        "programs": count,
        "seed": seed,
        "accepted": accepted,
        "rejected": rejected,
        "generated_valid_accepted": valid,
        "unexpected_exceptions": 0,
        "hangs": 0,
        "elapsed_s": time.monotonic() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=60_000)
    parser.add_argument("--seed", type=int, default=4721)
    parser.add_argument("--output", type=Path, default=Path("docs/reports/part-2-fuzz.json"))
    args = parser.parse_args()
    if args.count < 50_000:
        parser.error("proof requires at least 50,000 programs")
    result = run(args.count, args.seed)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
