# services/api/Dockerfile
#
# ==============================================================================
# MULTI-STAGE BUILD
# ==============================================================================
# A multi-stage build uses multiple FROM instructions in one Dockerfile.
# Each stage is a separate image layer. We only COPY the files we need from
# each stage into the final image. This means:
#   - Stage 1 (builder): has uv, all build tools, dev dependencies
#   - Stage 2 (final): only has the installed packages and our app code
#
# Result: the final image is ~50% smaller because it has no build tools,
# no uv, no Python package source files - just the compiled bytecode and
# the packages the app actually needs at runtime.
#
# WHAT IS uv?
# uv is an extremely fast Python package installer written in Rust.
# It replaces pip in this project. `uv sync` reads pyproject.toml and
# installs exactly the pinned versions from uv.lock.
#
# WHY UV_PROJECT_ENVIRONMENT?
# This env var tells uv to install packages into /opt/venv instead of the
# default .venv. We do this because:
#   - /opt/venv is a predictable, absolute path that the final stage can COPY
#   - .venv is relative to the project root and harder to copy cleanly
# ==============================================================================


# ==============================================================================
# STAGE 1: BUILDER
# Installs all Python dependencies for warden-api into an isolated venv.
# This stage has uv and all build dependencies, but we throw it away after.
# ==============================================================================
FROM python:3.12-slim AS builder

# Set the path where uv will install our packages.
# Setting this BEFORE installing tells uv to put everything here.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Install uv from its official installer script.
# We pin a specific version (0.7.x) so builds are reproducible.
# --no-cache prevents uv's installer from leaving temp files.
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /usr/local/bin/

# Set the working directory inside the builder stage.
WORKDIR /build

# Copy ONLY the files uv needs to install dependencies.
# We copy these BEFORE copying app code so Docker can cache the package
# installation layer. If you change only app code (not pyproject.toml or
# uv.lock), Docker reuses the cached install layer = much faster rebuilds.
COPY pyproject.toml uv.lock ./
COPY libs/ ./libs/
COPY services/api/ ./services/api/

# Install warden-api and all its dependencies into /opt/venv.
# Flags explained:
#   --frozen: fail if uv.lock is out of date (ensures reproducibility)
#   --no-dev:  skip dev dependencies (pytest, ruff, mypy - not needed in prod)
#   --no-editable: install as a regular package (not symlinked to source)
#   --package warden-api: install only this workspace member (not all members)
RUN uv sync --frozen --no-dev --no-editable --package warden-api


# ==============================================================================
# STAGE 2: FINAL IMAGE
# Only contains the installed venv and app code. No build tools.
# ==============================================================================
FROM python:3.12-slim AS final

# WHAT IS A NON-ROOT USER?
# ========================
# By default, Docker containers run as root (UID 0). This is dangerous:
# if an attacker exploits a vulnerability in your app, they get root access
# inside the container (which can sometimes escalate to the host).
#
# Best practice: create a dedicated user with a fixed UID and run the app
# as that user. UID 10001 is chosen to be in a range unlikely to conflict
# with system users (0-999) or regular user-created users (1000-9999).
RUN groupadd --gid 10001 warden && \
    useradd --uid 10001 --gid warden --no-create-home --shell /bin/false warden

# Copy the installed Python packages from the builder stage.
# Everything your app imports at runtime lives in this directory.
COPY --from=builder /opt/venv /opt/venv

# Copy the Alembic configuration and migration scripts.
# The /ready endpoint reads alembic.ini to find the head revision.
# The migrate service uses alembic.ini to run `alembic upgrade head`.
COPY --chown=warden:warden libs/warden_db/alembic.ini /app/db/alembic.ini
COPY --chown=warden:warden libs/warden_db/migrations/ /app/db/migrations/

# Make the venv's Python binary the default Python in PATH.
# This means `python` and `uvicorn` resolve to /opt/venv/bin/python
# without needing to activate the venv explicitly.
ENV PATH=/opt/venv/bin:$PATH

# Tell Python where to find the installed packages.
ENV PYTHONPATH=/opt/venv/lib/python3.12/site-packages

# Don't buffer stdout/stderr. Without this, print() output might not appear
# in `docker logs` until a buffer fills up (bad for debugging crashes).
ENV PYTHONUNBUFFERED=1

# Switch to the non-root user for all subsequent commands.
USER warden

# WORKDIR sets the default directory for CMD and ENTRYPOINT.
WORKDIR /app

# WHY HEALTHCHECK uses /live and NOT /health:
# ============================================
# Docker uses HEALTHCHECK to decide if the container is "healthy".
# If the health check fails, Docker marks the container unhealthy and
# dependent services (api) won't start (because migrate service must complete
# first, and api depends on migrate being service_completed_successfully).
#
# /health checks postgres + redis + objectstore. During the brief window
# between the container starting and migrations completing, postgres IS
# reachable but may reject queries (migrations running). This could make
# /health flap between ok and degraded, confusing orchestrators.
#
# /live is unconditional - it only checks that the process can respond to
# HTTP at all. It returns 200 immediately, always. This is the correct
# signal for Docker: "is the process alive?" not "are all deps up?"
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/live')" \
    || exit 1

# Start the API server using uvicorn.
# Flags explained:
#   warden_api.main:create_app  - module:object path to the app factory
#   --factory                   - tells uvicorn to CALL create_app() to get the app
#   --host 0.0.0.0              - listen on all interfaces INSIDE the container
#                                 (Docker handles the host-side port mapping;
#                                  0.0.0.0 inside a container is safe and required)
#   --port 8000                 - internal container port (mapped to 18000 on host)
CMD ["uvicorn", "warden_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
