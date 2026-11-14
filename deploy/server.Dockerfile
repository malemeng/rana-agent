# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

COPY pyproject.toml ./
COPY server ./server
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/server" \
    AGENT_DATA_DIR="/data"

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --no-create-home --home-dir /app agent \
    && mkdir -p /data /app \
    && chown -R agent:agent /data /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=agent:agent alembic.ini /app/alembic.ini
COPY --chown=agent:agent server /app/server

WORKDIR /app
USER agent
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]

CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn personal_agent.main:app --host 0.0.0.0 --port 8080 --proxy-headers"]
