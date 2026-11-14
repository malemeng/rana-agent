from .conftest import execute


def test_health_is_public_and_execute_requires_bearer(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}

    payload = {"task_id": "task-1", "tool": "workspace_list", "arguments": {}}
    missing = client.post("/api/v1/execute", json=payload)
    wrong = client.post(
        "/api/v1/execute",
        json=payload,
        headers={"Authorization": "Bearer wrong-token"},
    )
    basic = client.post(
        "/api/v1/execute",
        json=payload,
        headers={"Authorization": "Basic dGVzdDp0ZXN0"},
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert basic.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"

    valid = client.post(
        "/api/v1/execute",
        json=payload,
        headers={"Authorization": "Bearer test-token"},
    )
    assert valid.status_code == 200
    assert valid.json()["ok"] is True


def test_artifacts_require_bearer(client):
    response = execute(
        client,
        "workspace_write",
        {"path": "secret.txt", "content": "content"},
    )
    assert response.json()["ok"] is True
    assert client.get("/api/v1/artifacts/task-1/secret.txt").status_code == 401
    assert (
        client.get(
            "/api/v1/artifacts/task-1/secret.txt",
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )


def test_invalid_request_and_unknown_tool(client):
    bad_task = client.post(
        "/api/v1/execute",
        json={"task_id": "../other", "tool": "workspace_list", "arguments": {}},
        headers={"Authorization": "Bearer test-token"},
    )
    assert bad_task.status_code == 422

    unknown = execute(client, "not_a_tool")
    assert unknown.status_code == 200
    body = unknown.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "UNKNOWN_TOOL"
    assert body["duration_ms"] >= 0
