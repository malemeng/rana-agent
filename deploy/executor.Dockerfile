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

ARG INSTALL_BROWSER=false
RUN if [ "${INSTALL_BROWSER}" = "true" ]; then \
        /opt/venv/bin/pip install "playwright>=1.48,<2"; \
    fi

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app/executor:/app/server" \
    WORKSPACE_ROOT="/data/workspace" \
    PLAYWRIGHT_BROWSERS_PATH="/opt/ms-playwright"

RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --no-create-home --home-dir /app agent \
    && mkdir -p /data /app /opt/ms-playwright \
    && chown -R agent:agent /data /app /opt/ms-playwright

COPY --from=builder /opt/venv /opt/venv
COPY --chown=agent:agent server /app/server
COPY --chown=agent:agent executor /app/executor

ARG INSTALL_BROWSER=false
RUN if [ "${INSTALL_BROWSER}" = "true" ]; then \
        /opt/venv/bin/playwright install --with-deps chromium \
        && chmod -R a+rX /opt/ms-playwright; \
    fi

WORKDIR /app/executor
USER agent
EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/healthz', timeout=3)"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081", "--proxy-headers"]
