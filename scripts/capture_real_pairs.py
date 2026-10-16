"""CLI utility to capture real webpage snapshots over time and collect human labels.

USAGE:
1. Capture a URL snapshot:
   python scripts/capture_real_pairs.py https://example.com
   OR:
   make capture-pair URL=https://example.com

2. Interactively label pairs:
   python scripts/capture_real_pairs.py label <path_to_file1> <path_to_file2>
"""

import asyncio
import json
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from warden_fetch.tier1 import fetch_tier1

FIXTURES_DIR = Path("tests/fixtures/real_pairs")
LABELS_FILE = FIXTURES_DIR / "labels.json"


async def capture_url(url: str) -> Path:
    """Fetch URL via safe Tier-1 HTTP fetch and save raw HTML with timestamp."""
    parsed = urllib.parse.urlsplit(url)
    domain = parsed.netloc.replace(":", "_") or "unknown_domain"

    target_dir = FIXTURES_DIR / domain
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] Fetching {url} via Tier-1 safe fetch...")
    result = await fetch_tier1(url, timeout_s=15.0)

    if result.status != 200 or not result.body:
        print(f"[!] Fetch failed with HTTP {result.status} ({len(result.body)} bytes)")
        sys.exit(1)

    ts_str = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_file = target_dir / f"{ts_str}.html"
    out_file.write_bytes(result.body)

    print(f"[+] Saved snapshot to {out_file} ({len(result.body)} bytes)")
    return out_file


def label_pair(file1: Path, file2: Path) -> None:
    """Interactively label two snapshot HTML files and append to labels.json."""
    if not file1.is_file() or not file2.is_file():
        print(f"[!] Files do not exist: {file1}, {file2}")
        sys.exit(1)

    print("\n--- Comparing Snapshot Pair ---")
    print(f"File A: {file1} ({file1.stat().st_size} bytes)")
    print(f"File B: {file2} ({file2.stat().st_size} bytes)")

    ans = input("Is this a meaningful change? (y/n): ").strip().lower()
    meaningful = ans in ("y", "yes")

    LABELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    labels = []
    if LABELS_FILE.is_file():
        try:
            labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
        except Exception:
            labels = []

    record = {
        "file_a": str(file1),
        "file_b": str(file2),
        "timestamp": datetime.now(UTC).isoformat(),
        "meaningful": meaningful,
    }
    labels.append(record)
    LABELS_FILE.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    print(f"[+] Recorded label ({meaningful=}) to {LABELS_FILE}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/capture_real_pairs.py <URL> | label <file1> <file2>")
        sys.exit(1)

    if sys.argv[1] == "label":
        if len(sys.argv) < 4:
            print("Usage: python scripts/capture_real_pairs.py label <file1> <file2>")
            sys.exit(1)
        label_pair(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        url = sys.argv[1]
        asyncio.run(capture_url(url))


if __name__ == "__main__":
    main()
