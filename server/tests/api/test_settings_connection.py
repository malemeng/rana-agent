from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from personal_agent.config import Settings
from personal_agent.main import create_app


class FakeProvider:
    api_key = "configured"
    model = "fake-mimo"

    async def complete(self, messages, tools=None, model=None):
        return SimpleNamespace(content="ok")


def test_mimo_connection_endpoint(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        secret_key="test-secret-key-with-at-least-32-bytes",
        admin_password="admin-test-password",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.mimo_provider = FakeProvider()
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin-test-password"},
        )
        token = login.json()["access_token"]
        response = client.get(
            "/api/v1/settings/test-connection",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["response"] == "ok"
