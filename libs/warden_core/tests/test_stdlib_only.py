import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "warden_core"


def test_only_stdlib_imports():
    bad = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:  # relative import like "from . import x"
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top != "warden_core":
                    bad.append(f"{path.name}: {name}")
    assert not bad, f"non-stdlib imports found: {bad}"
