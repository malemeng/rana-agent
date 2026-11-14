"""Health watchdog with bounded Docker restarts for NAS deployments."""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import socket
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class Target:
    name: str
    url: str
    container: str


class DockerSocket:
    def __init__(self, socket_path: str = "/var/run/docker.sock") -> None:
        self.socket_path = socket_path

    def restart(self, container: str) -> None:
        class UnixConnection(http.client.HTTPConnection):
            def __init__(self, socket_path: str) -> None:
                super().__init__("docker", timeout=30)
                self.socket_path = socket_path

            def connect(self) -> None:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(30)
                self.sock.connect(self.socket_path)

        connection = UnixConnection(self.socket_path)
        try:
            connection.request("POST", f"/containers/{container}/restart?t=20")
            response = connection.getresponse()
            response.read()
            if response.status not in {204, 304}:
                raise RuntimeError(f"Docker restart returned HTTP {response.status}")
        finally:
            connection.close()


def probe(url: str) -> None:
    with urllib.request.urlopen(url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"health HTTP {response.status}")


class Watchdog:
    def __init__(
        self,
        targets: list[Target],
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300,
        max_restarts: int = 3,
        restart_window_seconds: float = 1800,
        startup_grace_seconds: float = 60,
        probe_fn: Callable[[str], None] = probe,
        docker: DockerSocket | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.targets = targets
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.max_restarts = max_restarts
        self.restart_window_seconds = restart_window_seconds
        self.startup_grace_seconds = startup_grace_seconds
        self.probe = probe_fn
        self.docker = docker or DockerSocket()
        self.clock = clock
        self.failures = {target.name: 0 for target in targets}
        self.last_attempt = {target.name: -float("inf") for target in targets}
        self.restarts = {target.name: [] for target in targets}
        self.started_at = clock()

    def _log(self, level: int, event: str, target: Target, **extra: object) -> None:
        logging.getLogger("personal_agent.watchdog").log(
            level,
            json.dumps(
                {
                    "time": datetime.now(UTC).isoformat(),
                    "event": event,
                    "service": target.name,
                    **extra,
                },
                ensure_ascii=False,
            ),
        )

    def run_once(self) -> None:
        now = self.clock()
        for target in self.targets:
            try:
                self.probe(target.url)
                if self.failures[target.name]:
                    self._log(logging.INFO, "recovered", target)
                self.failures[target.name] = 0
                continue
            except Exception as exc:
                self.failures[target.name] += 1
                self._log(
                    logging.WARNING,
                    "probe_failed",
                    target,
                    failures=self.failures[target.name],
                    error=type(exc).__name__,
                )
            if self.failures[target.name] < self.failure_threshold:
                continue
            if now - self.started_at < self.startup_grace_seconds:
                continue
            if now - self.last_attempt[target.name] < self.cooldown_seconds:
                continue
            recent = [
                timestamp
                for timestamp in self.restarts[target.name]
                if now - timestamp < self.restart_window_seconds
            ]
            self.restarts[target.name] = recent
            if len(recent) >= self.max_restarts:
                self._log(logging.ERROR, "restart_rate_limited", target, restarts=len(recent))
                continue
            self.last_attempt[target.name] = now
            try:
                self.docker.restart(target.container)
                self.restarts[target.name].append(now)
                self.failures[target.name] = 0
                self._log(logging.ERROR, "container_restarted", target, container=target.container)
            except Exception as exc:
                self._log(
                    logging.ERROR,
                    "restart_failed",
                    target,
                    container=target.container,
                    error=type(exc).__name__,
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Rana Agent NAS watchdog")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    prefix = os.getenv("WATCHDOG_CONTAINER_PREFIX", "rana-agent")
    server_url = os.getenv("WATCHDOG_SERVER_URL", "http://server:8080/readyz")
    executor_url = os.getenv("WATCHDOG_EXECUTOR_URL", "http://executor:8081/healthz")
    targets = [
        Target("server", server_url, f"{prefix}-server-1"),
        Target("executor", executor_url, f"{prefix}-executor-1"),
    ]
    watchdog = Watchdog(
        targets,
        failure_threshold=int(os.getenv("WATCHDOG_FAILURE_THRESHOLD", "3")),
        cooldown_seconds=float(os.getenv("WATCHDOG_COOLDOWN_SECONDS", "300")),
        max_restarts=int(os.getenv("WATCHDOG_MAX_RESTARTS", "3")),
        startup_grace_seconds=float(os.getenv("WATCHDOG_STARTUP_GRACE_SECONDS", "60")),
    )
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    while True:
        watchdog.run_once()
        if args.once:
            return
        time.sleep(float(os.getenv("WATCHDOG_INTERVAL_SECONDS", "30")))


if __name__ == "__main__":
    main()
