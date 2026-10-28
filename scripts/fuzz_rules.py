"""Run the seeded rule-language fuzz test driver under an external watchdog."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "libs/warden_rules/tests/fuzz_runner.py"),
        run_name="__main__",
    )
