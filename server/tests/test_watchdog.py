from scripts.watchdog import Target, Watchdog


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def restart(self, container: str) -> None:
        self.calls.append(container)


def test_watchdog_threshold_cooldown_and_restart_budget() -> None:
    now = 1000.0
    docker = FakeDocker()

    def clock() -> float:
        return now

    def fail(_: str) -> None:
        raise ConnectionError("down")

    watchdog = Watchdog(
        [Target("server", "http://server/readyz", "personal-agent-server-1")],
        failure_threshold=3,
        cooldown_seconds=10,
        max_restarts=2,
        restart_window_seconds=3600,
        startup_grace_seconds=0,
        probe_fn=fail,
        docker=docker,
        clock=clock,
    )
    for _ in range(3):
        watchdog.run_once()
    assert docker.calls == ["personal-agent-server-1"]
    watchdog.run_once()
    assert len(docker.calls) == 1
    now += 11
    for _ in range(3):
        watchdog.run_once()
    assert len(docker.calls) == 2
    now += 11
    for _ in range(3):
        watchdog.run_once()
    assert len(docker.calls) == 2
    assert watchdog.failures["server"] == 4


def test_watchdog_recovery_resets_failure_count() -> None:
    attempts = 0
    docker = FakeDocker()

    def intermittent(_: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts != 3:
            raise ConnectionError("down")

    watchdog = Watchdog(
        [Target("executor", "http://executor/healthz", "executor-1")],
        failure_threshold=3,
        startup_grace_seconds=0,
        probe_fn=intermittent,
        docker=docker,
    )
    for _ in range(5):
        watchdog.run_once()
    assert docker.calls == []
