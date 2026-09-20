.PHONY: fmt lint types test check

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run ruff format --check .

types:
	uv run mypy libs/warden_core/src libs/warden_db/src services/api/src

test:
	uv run pytest -q

check: lint types test
