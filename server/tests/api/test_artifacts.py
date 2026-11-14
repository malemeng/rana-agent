from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from personal_agent.config import Settings
from personal_agent.main import create_app
from personal_agent.models import Artifact, Goal, Task, User
from sqlalchemy import select


class FakeExecutorClient:
    async def get(self, path: str) -> httpx.Response:
        assert "/api/v1/artifacts/" in path and path.endswith("/report.md")
        return httpx.Response(200, content=b"report-content", request=httpx.Request("GET", path))


def test_artifact_download_is_proxied_with_owner_auth(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        secret_key="artifact-test-key-with-at-least-32-bytes",
        admin_password="admin-test-password",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin-test-password"},
        )
        token = login.json()["access_token"]

        async def create_artifact() -> str:
            async with app.state.session_factory() as session:
                user = await session.scalar(select(User).where(User.username == "admin"))
                goal = Goal(user_id=user.id, title="Report", input="Report")
                session.add(goal)
                await session.flush()
                task = Task(user_id=user.id, goal_id=goal.id, title="Report")
                session.add(task)
                await session.flush()
                artifact = Artifact(
                    user_id=user.id,
                    task_id=task.id,
                    name="report.md",
                    path="report.md",
                    content_type="text/markdown",
                    size=14,
                )
                session.add(artifact)
                await session.commit()
                return artifact.id

        artifact_id = client.portal.call(create_artifact)
        app.state.executor = SimpleNamespace(client=FakeExecutorClient())
        response = client.get(
            f"/api/v1/activities/artifacts/{artifact_id}/download",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.content == b"report-content"
        assert response.headers["content-type"].startswith("text/markdown")
        assert client.get(f"/api/v1/activities/artifacts/{artifact_id}/download").status_code == 401
