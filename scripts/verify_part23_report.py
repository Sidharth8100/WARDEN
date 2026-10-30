"""Verify exact source excerpts and the requested report structure, without dependencies."""

import hashlib
import json
import re
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    report = (root / "docs/reports/parts-2-3.md").read_text()
    manifest = json.loads((root / "docs/reports/parts-2-3-sources.json").read_text())
    for record in manifest:
        path = (root / record["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("source path outside repository")
        lines = path.read_text().splitlines(keepends=True)
        start, count = record["start"] - 1, record["lines"]
        excerpt = "".join(lines[start : start + count])
        if not 20 <= count <= 40:
            raise ValueError("excerpt must contain 20-40 source lines")
        if hashlib.sha256(excerpt.encode()).hexdigest() != record["excerpt_sha256"]:
            raise ValueError(f"stale source excerpt: {record['path']}:{start + 1}")
        if "```text\n" + excerpt + "```" not in report:
            raise ValueError(f"missing excerpt in report: {record['path']}")
    sections = re.findall(r"^## ([1-9])\.", report, flags=re.MULTILINE)
    if sections != list("123456789"):
        raise ValueError("report requires nine ordered sections")
    questions = re.findall(
        r"^### Q\d+\..*?\n\n(.*?)(?=^### Q\d+\.|^## 6\.)",
        report,
        flags=re.MULTILINE | re.DOTALL,
    )
    if len(questions) < 25 or report.count("**Gotcha:**") < 5:
        raise ValueError("interview bank requires 25 answers and five gotchas")
    for number, answer in enumerate(questions, 1):
        sentences = re.split(r"[.!?]\s+", answer.strip())
        if not 3 <= len(sentences) <= 6:
            raise ValueError(f"Q{number:02} has {len(sentences)} sentences; expected 3-6")
    print(f"Verified {len(manifest)} source excerpts, nine sections and {len(questions)} answers.")


if __name__ == "__main__":
    main()
