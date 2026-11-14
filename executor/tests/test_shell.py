import os
import shlex
import sys

from .conftest import execute


def test_shell_argv_runs_in_task_workspace(client):
    assert (
        execute(
            client, "workspace_write", {"path": "input.txt", "content": "from-workspace"}
        ).json()["ok"]
        is True
    )
    response = execute(
        client,
        "shell",
        {
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path('input.txt').read_text())",
            ]
        },
    )
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["exit_code"] == 0
    assert body["data"]["stdout"].strip() == "from-workspace"


def test_shell_command_uses_safe_shlex_split(client):
    executable = f'"{sys.executable}"' if os.name == "nt" else shlex.quote(sys.executable)
    code = "print('shlex-ok')"
    quoted_code = f'"{code}"' if os.name == "nt" else shlex.quote(code)
    response = execute(
        client,
        "shell",
        {"command": f"{executable} -c {quoted_code}"},
    )
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["stdout"].strip() == "shlex-ok"


def test_shell_timeout_kills_process_and_returns_exit_code(client):
    response = execute(
        client,
        "shell",
        {
            "argv": [
                sys.executable,
                "-c",
                "import time; print('started', flush=True); time.sleep(5)",
            ]
        },
        timeout_seconds=0.05,
    )
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "SHELL_TIMEOUT"
    assert body["data"]["exit_code"] is None
    assert body["duration_ms"] < 4000


def test_shell_stdout_and_stderr_are_independently_limited(client):
    response = execute(
        client,
        "shell",
        {
            "argv": [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 100); sys.stderr.write('y' * 100)",
            ]
        },
    )
    body = response.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["stdout"] == "x" * 32
    assert data["stderr"] == "y" * 32
    assert data["stdout_bytes"] == 100
    assert data["stderr_bytes"] == 100
    assert data["stdout_truncated"] is True
    assert data["stderr_truncated"] is True
    assert data["output_limit_bytes"] == 32


def test_shell_nonzero_exit_is_structured(client):
    response = execute(
        client,
        "shell",
        {"argv": [sys.executable, "-c", "raise SystemExit(7)"]},
    )
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "NON_ZERO_EXIT"
    assert body["data"]["exit_code"] == 7


def test_shell_missing_executable_is_structured(client):
    response = execute(
        client,
        "shell",
        {"argv": ["definitely-not-an-executable-executor-test"]},
    )
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "EXECUTABLE_NOT_FOUND"
