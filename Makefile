# WARDEN project Makefile
#
# WHAT IS A MAKEFILE?
# ====================
# Make is a build automation tool. Each `target:` in this file is a command
# you can run as `make <target>` from the project root. Make checks if the
# target's prerequisites have changed and only re-runs what's needed.
#
# We use it as a command shortcut: `make check` runs lint + types + tests in
# one shot. This is faster and less error-prone than typing all three commands.
#
# IMPORTANT: Indentation in Makefiles MUST use TABs, not spaces.
# If you see a "missing separator" error, check that there are real TABs.

# .PHONY: tells Make that these are not file names - just command names.
# Without this, if a file named "test" existed, `make test` would say "up to date".
.PHONY: fmt lint types test check check-todos up down logs migrate-test revision

# ==============================================================================
# Code quality targets
# ==============================================================================

fmt:
	# Apply formatting and auto-fix lint rules to ALL Python files.
	# Run this before committing to clean up your code automatically.
	uv run ruff format .
	uv run ruff check --fix .

lint:
	# Check formatting and lint rules WITHOUT making any changes.
	# Fails with a non-zero exit code if anything is wrong (used in CI).
	uv run ruff check .
	uv run ruff format --check .

types:
	# Run mypy static type checker on all source directories.
	# strict mode: no Any, no missing return types, no untyped functions.
	uv run mypy libs/warden_core/src libs/warden_db/src libs/warden_net/src services/api/src

test:
	# Run all tests (unit + integration if Postgres is reachable).
	# -q: quiet mode (one dot per test instead of full test name)
	uv run pytest -q

check-todos:
	# Run tests for student-implemented functions (tagged with @pytest.mark.student_todo)
	uv run pytest -q -m student_todo

check: lint types test
	# The "golden gate" - run ALL quality gates in sequence.
	# Every commit must pass `make check` before pushing.
	# Order matters: lint fast-fails before the slower mypy/pytest.

# ==============================================================================
# Docker Compose targets
# ==============================================================================

up:
	# Build images (if changed) and start ALL services in detached mode (-d).
	# The first time: builds the Dockerfile, pulls base images, starts everything.
	# Subsequent times: only rebuilds if Dockerfile or source changed.
	# migrate runs first (applies DB migrations), then api starts.
	docker compose up --build -d

down:
	# Stop and remove containers, but KEEP named volumes (pgdata, redisdata, etc.)
	# WARNING: Never use `down -v` in this project - it would delete all your data!
	# The `-v` flag is explicitly EXCLUDED here by project constraint.
	docker compose down

logs:
	# Stream logs from ALL running services to your terminal.
	# Ctrl+C to stop streaming (containers keep running).
	# Add a service name to filter: make logs SERVICE=api
	docker compose logs -f $(SERVICE)

migrate-test:
	# Container-based migration round-trip test: upgrade -> downgrade -> upgrade.
	# This proves that both upgrade() and downgrade() work correctly,
	# which is crucial: if downgrade() is broken, you can't roll back a bad deploy.
	#
	# HOW IT WORKS:
	# 1. Builds the image (same Dockerfile as production)
	# 2. Runs `alembic upgrade head` - applies all migrations
	# 3. Runs `alembic downgrade base` - removes ALL tables
	# 4. Runs `alembic upgrade head` again - re-applies everything
	# If all three steps succeed, the migration is proven reversible.
	docker compose run --rm migrate alembic -c /app/db/alembic.ini upgrade head
	docker compose run --rm migrate alembic -c /app/db/alembic.ini downgrade base
	docker compose run --rm migrate alembic -c /app/db/alembic.ini upgrade head

revision:
	# Generate a new Alembic migration file based on model changes.
	# Usage: make revision m="add_users_table"
	# This compares your ORM models against the current DB and generates
	# a migration script with the differences. Always review it before committing!
	#
	# The migration runs inside the compose network so it can reach Postgres
	# by the "postgres" service name.
	docker compose run --rm \
		-e WARDEN_DATABASE_URL="postgresql+asyncpg://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@postgres:5432/$${POSTGRES_DB}" \
		migrate \
		alembic -c /app/db/alembic.ini revision --autogenerate -m "$(m)"
