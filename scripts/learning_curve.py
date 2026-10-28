"""Run the isolated, seeded chaos-web learning-curve evaluation harness."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "sim/chaos_web/tests/learning_curve.py"),
        run_name="__main__",
    )
