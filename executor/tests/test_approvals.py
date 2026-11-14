def test_browser_write_requires_approval_before_playwright(client, auth_headers):
    response = client.post(
        "/api/v1/execute",
        json={
            "task_id": "task-1",
            "tool": "browser",
            "arguments": {"action": "click", "selector": "#submit"},
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["approval_required"] is True
    assert body["ok"] is True


def test_approved_browser_action_reaches_tool(client, auth_headers):
    response = client.post(
        "/api/v1/execute",
        json={
            "task_id": "task-1",
            "tool": "browser",
            "arguments": {"action": "click", "selector": "#submit", "url": "https://example.com"},
            "approved": True,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["approval_required"] is False
    assert body["ok"] is False
    assert body["error"]["code"] == "BROWSER_UNAVAILABLE"
