FROM python:3.12-slim AS builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /usr/local/bin/

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY libs/ ./libs/
COPY services/ ./services/
COPY sim/ ./sim/

RUN uv sync --frozen --no-dev --no-editable --package chaos-web

FROM python:3.12-slim AS final

RUN groupadd --gid 10001 chaos && \
    useradd --uid 10001 --gid chaos --no-create-home --shell /bin/false chaos

COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH
ENV PYTHONPATH=/opt/venv/lib/python3.12/site-packages
ENV PYTHONUNBUFFERED=1

USER chaos
WORKDIR /app

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

CMD ["uvicorn", "chaos_web.main:app", "--host", "0.0.0.0", "--port", "8000"]
