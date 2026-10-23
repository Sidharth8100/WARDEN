FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY libs/ ./libs/
COPY services/ ./services/
COPY sim/ ./sim/
RUN uv sync --frozen --no-dev --no-editable --package warden-fetcher

FROM python:3.12-slim
RUN groupadd --gid 10001 warden && useradd --uid 10001 --gid warden --no-create-home warden
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
RUN python -c "import importlib.util; assert all(importlib.util.find_spec(p) is None for p in ('sqlalchemy', 'asyncpg', 'warden_db'))"
USER warden
WORKDIR /app
CMD ["python", "-m", "warden_fetcher.main"]
