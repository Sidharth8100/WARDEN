"""Enforce import boundary constraints for libs/warden_fetch via AST traversal."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "warden_fetch"

FORBIDDEN_MODULES = {"sqlalchemy", "fastapi", "warden_db"}


def test_warden_fetch_import_boundaries() -> None:
    """Verify warden_fetch never imports sqlalchemy, fastapi, or warden_db.

    ARCHITECTURE INVARIANT:
    libs/warden_fetch executes network operations and lives inside untrusted worker
    zones. It must remain strictly decoupled from the database and web framework.
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

    assert not bad, f"Forbidden imports found in warden_fetch: {bad}"
