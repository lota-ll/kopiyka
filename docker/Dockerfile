# syntax=docker/dockerfile:1.7
# Multi-stage: builder збирає wheel'и, runtime лишається мінімальним.
# Мета — маленький образ і відсутність компілятора у production.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# ---------------------------------------------------------------------------

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Непривілейований користувач: контейнер, що працює під root, — це
# зайвий крок для зловмисника при будь-якому RCE.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app migrations /app/migrations
COPY --chown=app:app alembic.ini /app/alembic.ini
COPY --chown=app:app seeds /app/seeds
COPY --chown=app:app scripts /app/scripts

ENV KOPIYKA_SEEDS_DIR=/app/seeds

WORKDIR /app
USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "kopiyka.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
