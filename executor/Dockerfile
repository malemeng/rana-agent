FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKSPACE_ROOT=/workspace \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PORT=8000

WORKDIR /opt/executor

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

ARG INSTALL_PLAYWRIGHT=false
RUN mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" && \
    if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
      pip install --no-cache-dir "playwright>=1.45.0,<2.0.0" && \
      playwright install --with-deps chromium; \
    fi && \
    chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"

COPY app ./app
RUN groupadd --gid 10001 executor && \
    useradd --uid 10001 --gid executor --no-create-home executor && \
    mkdir -p /workspace && \
    chown -R executor:executor /workspace /opt/executor

USER executor
EXPOSE 8000
VOLUME ["/workspace"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/healthz', timeout=2)" || exit 1

CMD ["python", "-m", "app"]
