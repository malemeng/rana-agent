# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WATCHDOG_CONTAINER_PREFIX=rana-agent

WORKDIR /opt/watchdog
COPY scripts/watchdog.py /opt/watchdog/watchdog.py

# The container has no cloud credentials or application data. Access is limited
# to the internal network and the Docker socket used for bounded restarts.
CMD ["python", "/opt/watchdog/watchdog.py"]
