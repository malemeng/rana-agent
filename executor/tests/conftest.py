import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        token="test-token",
        workspace_root=tmp_path / "workspace",
        max_file_bytes=128,
        max_list_entries=100,
        shell_output_bytes=32,
        default_timeout_seconds=2.0,
        max_timeout_seconds=5.0,
        browser_headless=True,
        log_level="WARNING",
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def execute(
    client: TestClient,
    tool: str,
    arguments: dict | None = None,
    *,
    task_id: str = "task-1",
    timeout_seconds: float | None = None,
):
    payload = {
        "task_id": task_id,
        "tool": tool,
        "arguments": arguments or {},
    }
    if timeout_seconds is not None:
        payload["timeout_seconds"] = timeout_seconds
    return client.post(
        "/api/v1/execute",
        json=payload,
        headers={"Authorization": "Bearer test-token"},
    )
