"""Enforce import boundary constraints for libs/warden_compare via AST traversal."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "warden_compare"

FORBIDDEN_MODULES = {"httpx", "selenium", "aioboto3", "sqlalchemy", "fastapi", "warden_db"}


def test_warden_compare_import_boundaries() -> None:
    """Verify warden_compare never imports network, db, or framework libraries.

    ARCHITECTURE INVARIANT:
    libs/warden_compare handles pure byte transformation, DOM parsing, diffing, and
    classification. It has ZERO network-capable dependencies installed (not httpx,
    not selenium, not aioboto3) and no database or web framework dependencies. It is
    structurally incapable of making network calls or touching the database.
    """
    bad: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue
                names = [node.module or ""]
            else:
                continue

            for name in names:
                top = name.split(".")[0]
                if top in FORBIDDEN_MODULES:
                    bad.append(f"{path.name}: {name}")

    assert not bad, f"Forbidden imports found in warden_compare: {bad}"
