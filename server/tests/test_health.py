from pathlib import Path

from fastapi.testclient import TestClient
from personal_agent.config import Settings
from personal_agent.main import create_app


def test_health_and_security_headers(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, secret_key="test-secret")
    with TestClient(create_app(settings)) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/readyz").json()["status"] == "ready"
        assert client.get("/").json()["docs"] == "/docs"
