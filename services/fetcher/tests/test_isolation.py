"""Import boundaries and startup defense; image-level inspection remains separate."""

import ast
import tomllib
from pathlib import Path

import pytest

from warden_fetcher.main import assert_no_database_access


def test_startup_rejects_database_url_even_if_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WARDEN_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert_no_database_access()
    monkeypatch.setenv("WARDEN_DATABASE_URL", "")
    with pytest.raises(RuntimeError):
        assert_no_database_access()


def test_transitive_internal_dependencies_and_source_are_database_free() -> None:
    root = Path(__file__).resolve().parents[3]
    visited = set()
    manifests = [*root.glob("libs/*/pyproject.toml"), root / "services/fetcher/pyproject.toml"]
    packages = {tomllib.loads(path.read_text())["project"]["name"]: path for path in manifests}

    def inspect(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        manifest = packages[name]
        deps = tomllib.loads(manifest.read_text())["project"].get("dependencies", [])
        assert not any(
            dep.split(">=")[0].split("[")[0] in {"sqlalchemy", "asyncpg", "warden-db", "fastapi"}
            for dep in deps
        )
        for path in (manifest.parent / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                assert not any(
                    module.split(".")[0]
                    in {
                        "sqlalchemy",
                        "asyncpg",
                        "warden_db",
                        "warden_api",
                        "warden_scheduler",
                        "warden_processor",
                    }
                    for module in modules
                )
        for dep in deps:
            if dep in packages:
                inspect(dep)

    inspect("warden-fetcher")
    assert "warden-fetch" in visited and "warden-redis" in visited
