"""Two-process HTTP smoke test for local development or a NAS deployment."""

from __future__ import annotations

import argparse
import json
import time

import httpx


def wait_ready(client: httpx.Client, url: str) -> None:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            response = client.get(url)
            if response.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"Service not ready: {url}: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8080")
    parser.add_argument("--executor", default="http://127.0.0.1:8081")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--internal-token", required=True)
    args = parser.parse_args()

    with httpx.Client(timeout=10, trust_env=False) as client:
        wait_ready(client, f"{args.server}/healthz")
        wait_ready(client, f"{args.executor}/healthz")
        login = client.post(
            f"{args.server}/api/v1/auth/login",
            json={"username": args.username, "password": args.password},
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        internal = {"Authorization": f"Bearer {args.internal_token}"}
        me = client.get(f"{args.server}/api/v1/auth/me", headers=auth)
        me.raise_for_status()
        goals = client.get(f"{args.server}/api/v1/goals", headers=auth)
        goals.raise_for_status()
        write = client.post(
            f"{args.executor}/api/v1/execute",
            headers=internal,
            json={
                "task_id": "smoke-task",
                "tool": "workspace_write",
                "arguments": {"path": "result.txt", "content": "smoke-ok"},
            },
        )
        write.raise_for_status()
        assert write.json()["ok"] is True
        artifact = client.get(
            f"{args.executor}/api/v1/artifacts/smoke-task/result.txt",
            headers=internal,
        )
        artifact.raise_for_status()
        assert artifact.text == "smoke-ok"
        anonymous = client.get(f"{args.server}/api/v1/goals")
        assert anonymous.status_code == 401
        print(
            json.dumps(
                {
                    "server": "ok",
                    "executor": "ok",
                    "login": "ok",
                    "artifact": artifact.text,
                    "anonymous_status": anonymous.status_code,
                    "existing_goals": len(goals.json()),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
