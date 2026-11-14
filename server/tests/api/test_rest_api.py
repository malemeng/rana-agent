from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from personal_agent.api import include_rest_routes
from personal_agent.config import Settings
from personal_agent.db import init_database, make_engine, make_session_factory
from personal_agent.models import (
    Approval,
    Artifact,
    Setting,
    Task,
    TaskEvent,
    User,
)
from personal_agent.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from personal_agent.services.tasks import TaskManager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect


class FakeProvider:
    def complete(self, messages, tools=None, model=None):
        raise AssertionError("REST API tests do not execute an agent")

    async def stream_chat(self, messages, model=None):
        raise AssertionError("REST API tests do not execute an agent")
        yield ""


@dataclass
class APIContext:
    client: TestClient
    session_factory: async_sessionmaker[AsyncSession]
    manager: TaskManager
    user_id: str
    other_user_id: str

    def run(self, awaitable):
        async def invoke():
            return await awaitable

        return self.client.portal.call(invoke)

    def login(self, username: str = "owner") -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "test-password"},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def auth(self, username: str = "owner") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.login(username)['access_token']}"}


async def _seed_users(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    async with session_factory() as session:
        password = hash_password("test-password")
        owner = User(username="owner", password_hash=password)
        other = User(username="other", password_hash=password)
        session.add_all([owner, other])
        await session.commit()
        return owner.id, other.id


@pytest.fixture
def api(tmp_path) -> Iterator[APIContext]:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'api-test.db'}")
    session_factory = make_session_factory(engine)
    settings = Settings(
        secret_key="api-test-secret-that-is-long-and-persistent",
        access_token_minutes=30,
        refresh_token_days=7,
    )
    manager = TaskManager(session_factory, FakeProvider(), settings=settings)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.task_manager = manager
    include_rest_routes(app)
    with TestClient(app) as client:
        client.portal.call(init_database, engine)
        owner_id, other_id = client.portal.call(_seed_users, session_factory)
        yield APIContext(client, session_factory, manager, owner_id, other_id)
        client.portal.call(manager.events.close)
        client.portal.call(engine.dispose)


def test_scrypt_and_access_token_primitives() -> None:
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("anything", "malformed")
    token = create_access_token(
        "user-1", secret_key="secret-key-with-at-least-32-bytes", expires_minutes=5
    )
    claims = decode_access_token(token, secret_key="secret-key-with-at-least-32-bytes")
    assert claims["sub"] == "user-1"
    assert claims["type"] == "access"
    assert {"exp", "iat", "jti"} <= claims.keys()


def test_auth_login_rotation_logout(api: APIContext) -> None:
    assert api.client.get("/api/v1/auth/me").status_code == 401
    wrong = api.client.post("/api/v1/auth/login", json={"username": "owner", "password": "wrong"})
    assert wrong.status_code == 401

    tokens = api.login()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = api.client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["id"] == api.user_id
    assert me.json()["username"] == "owner"

    rotated_response = api.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert rotated_response.status_code == 200
    rotated = rotated_response.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert rotated["access_token"] != tokens["access_token"]
    reused = api.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401

    rotated_headers = {"Authorization": f"Bearer {rotated['access_token']}"}
    assert api.client.get("/api/v1/auth/me", headers=rotated_headers).status_code == 200
    logout = api.client.post(
        "/api/v1/auth/logout",
        headers=rotated_headers,
        json={"refresh_token": rotated["refresh_token"]},
    )
    assert logout.status_code == 204
    revoked = api.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    )
    assert revoked.status_code == 401


def test_conversation_goal_task_runtime_and_owner_isolation(api: APIContext) -> None:
    headers = api.auth()
    other_headers = api.auth("other")
    created = api.client.post("/api/v1/conversations", headers=headers, json={"title": "计划"})
    assert created.status_code == 201, created.text
    conversation = created.json()
    assert conversation["title"] == "计划"
    assert (
        api.client.get(
            f"/api/v1/conversations/{conversation['id']}", headers=other_headers
        ).status_code
        == 404
    )
    assert api.client.get("/api/v1/conversations", headers=other_headers).json() == []

    renamed = api.client.patch(
        f"/api/v1/conversations/{conversation['id']}",
        headers=headers,
        json={"title": "今日计划", "summary": "摘要"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "今日计划"
    accepted = api.client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=headers,
        json={"content": "帮我整理今天的事项"},
    )
    assert accepted.status_code == 202, accepted.text
    message_task = accepted.json()
    assert message_task["message"]["role"] == "user"
    assert message_task["task"]["kind"] == "conversation"
    messages = api.client.get(
        f"/api/v1/conversations/{conversation['id']}/messages", headers=headers
    )
    assert [item["content"] for item in messages.json()] == ["帮我整理今天的事项"]

    goal_response = api.client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "title": "整理计划",
            "input": "列出今天最重要的三件事",
            "priority": 70,
            "idempotency_key": "goal-request-1",
        },
    )
    assert goal_response.status_code == 201, goal_response.text
    body = goal_response.json()
    goal = body["goal"]
    task = body["task"]
    assert goal["status"] == "active"
    assert task["goal_id"] == goal["id"]
    duplicate = api.client.post(
        "/api/v1/goals",
        headers=headers,
        json={
            "title": "整理计划",
            "input": "列出今天最重要的三件事",
            "idempotency_key": "goal-request-1",
        },
    )
    assert duplicate.json()["goal"]["id"] == goal["id"]
    assert duplicate.json()["task"]["id"] == task["id"]

    assert api.client.get(f"/api/v1/goals/{goal['id']}", headers=other_headers).status_code == 404
    assert api.client.get(f"/api/v1/tasks/{task['id']}", headers=other_headers).status_code == 404
    filtered = api.client.get("/api/v1/tasks", headers=headers, params={"goal_id": goal["id"]})
    assert [item["id"] for item in filtered.json()] == [task["id"]]
    updated = api.client.patch(
        f"/api/v1/tasks/{task['id']}", headers=headers, json={"priority": 90}
    )
    assert updated.json()["priority"] == 90
    succeeded = api.client.post(
        f"/api/v1/tasks/{task['id']}/status",
        headers=headers,
        json={"status": "succeeded"},
    )
    assert succeeded.status_code == 200
    assert succeeded.json()["progress"] == 100
    retried = api.client.post(f"/api/v1/tasks/{task['id']}/retry", headers=headers)
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"
    cancelled = api.client.post(f"/api/v1/tasks/{task['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    paused = api.client.post(
        f"/api/v1/goals/{goal['id']}/status", headers=headers, json={"status": "paused"}
    )
    assert paused.json()["status"] == "paused"
    completed = api.client.patch(
        f"/api/v1/goals/{goal['id']}", headers=headers, json={"status": "completed"}
    )
    assert completed.json()["completed_at"] is not None

    assert api.client.delete(f"/api/v1/tasks/{task['id']}", headers=headers).status_code == 204
    assert api.client.delete(f"/api/v1/goals/{goal['id']}", headers=headers).status_code == 204
    assert (
        api.client.delete(
            f"/api/v1/conversations/{conversation['id']}", headers=headers
        ).status_code
        == 204
    )


def test_memory_schedule_settings_and_connections(api: APIContext) -> None:
    headers = api.auth()
    other_headers = api.auth("other")
    memory_response = api.client.post(
        "/api/v1/memories",
        headers=headers,
        json={"content": "喜欢 concise 周报", "tags": ["preference", "周报"]},
    )
    assert memory_response.status_code == 201, memory_response.text
    memory = memory_response.json()
    pinned = api.client.patch(
        f"/api/v1/memories/{memory['id']}", headers=headers, json={"pinned": True}
    )
    assert pinned.json()["pinned"] is True
    found = api.client.get("/api/v1/memories", headers=headers, params={"q": "周报"})
    assert len(found.json()) == 1
    assert (
        api.client.get(f"/api/v1/memories/{memory['id']}", headers=other_headers).status_code == 404
    )

    schedule_response = api.client.post(
        "/api/v1/schedules",
        headers=headers,
        json={
            "title": "周报",
            "prompt": "生成周报",
            "cron": "*/5 * * * *",
            "timezone": "Asia/Shanghai",
        },
    )
    assert schedule_response.status_code == 201, schedule_response.text
    schedule = schedule_response.json()
    assert schedule["next_run_at"] is not None
    invalid = api.client.patch(
        f"/api/v1/schedules/{schedule['id']}", headers=headers, json={"cron": "bad cron"}
    )
    assert invalid.status_code == 422
    disabled = api.client.patch(
        f"/api/v1/schedules/{schedule['id']}", headers=headers, json={"enabled": False}
    )
    assert disabled.json()["enabled"] is False

    secret_response = api.client.post(
        "/api/v1/settings",
        headers=headers,
        json={"key": "mimo_api_key", "value": "raw-secret-value"},
    )
    assert secret_response.status_code == 201
    assert secret_response.json()["value"] == "********"
    assert secret_response.json()["is_secret"] is True
    fetched_secret = api.client.get("/api/v1/settings/mimo_api_key", headers=headers)
    assert fetched_secret.status_code == 200
    assert fetched_secret.json()["value"] == "********"
    preserved = api.client.put(
        "/api/v1/settings/mimo_api_key", headers=headers, json={"value": "********"}
    )
    assert preserved.json()["value"] == "********"
    theme = api.client.put("/api/v1/settings/theme", headers=headers, json={"value": "dark"})
    assert theme.json()["value"] == "dark"
    values = api.client.get("/api/v1/settings", headers=headers).json()
    assert "raw-secret-value" not in str(values)
    assert api.client.get("/api/v1/settings/mimo_api_key", headers=other_headers).status_code == 404

    async def stored_secret() -> str:
        async with api.session_factory() as session:
            setting = (
                await session.scalars(
                    select(Setting).where(
                        Setting.user_id == api.user_id, Setting.key == "mimo_api_key"
                    )
                )
            ).one()
            return setting.value_json

    assert api.run(stored_secret()) == "raw-secret-value"

    connection_response = api.client.post(
        "/api/v1/connections",
        headers=headers,
        json={
            "kind": "generic",
            "name": "NAS",
            "config": {
                "url": "https://nas.internal",
                "api_token": "connection-secret",
                "nested": {"password": "nested-secret", "port": 443},
            },
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    connection = connection_response.json()
    assert connection["config"]["api_token"] == "********"
    assert connection["config"]["nested"]["password"] == "********"
    assert connection["config"]["nested"]["port"] == 443
    connection_update = api.client.patch(
        f"/api/v1/connections/{connection['id']}",
        headers=headers,
        json={"name": "Primary NAS", "config": connection["config"]},
    )
    assert connection_update.status_code == 200
    assert connection_update.json()["config"]["api_token"] == "********"
    assert (
        api.client.get(f"/api/v1/connections/{connection['id']}", headers=other_headers).status_code
        == 404
    )

    async def stored_connection_secret() -> str:
        async with api.session_factory() as session:
            from personal_agent.models import Connection

            row = await session.get(Connection, connection["id"])
            return row.config_json["api_token"]

    assert api.run(stored_connection_secret()) == "connection-secret"
    assert api.client.delete(f"/api/v1/memories/{memory['id']}", headers=headers).status_code == 204
    assert (
        api.client.delete(f"/api/v1/schedules/{schedule['id']}", headers=headers).status_code == 204
    )
    assert api.client.delete("/api/v1/settings/theme", headers=headers).status_code == 204
    assert (
        api.client.delete(f"/api/v1/connections/{connection['id']}", headers=headers).status_code
        == 204
    )


async def _seed_approval_activity(
    session_factory: async_sessionmaker[AsyncSession], user_id: str, other_user_id: str
) -> dict[str, Any]:
    async with session_factory() as session:
        task = Task(user_id=user_id, title="执行高风险步骤", status="waiting_approval")
        session.add(task)
        await session.flush()
        approval = Approval(
            user_id=user_id,
            task_id=task.id,
            action="executor.write",
            payload_json={"path": "/tmp/report.txt"},
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
        )
        other_approval = Approval(
            user_id=other_user_id,
            task_id=task.id,
            action="other",
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
        )
        event = TaskEvent(user_id=user_id, task_id=task.id, type="task.waiting", payload_json={})
        other_event = TaskEvent(user_id=other_user_id, type="private", payload_json={})
        artifact = Artifact(
            user_id=user_id, task_id=task.id, name="report.txt", path="/tmp/report.txt"
        )
        session.add_all([approval, other_approval, event, other_event, artifact])
        await session.commit()
        return {
            "task_id": task.id,
            "approval_id": approval.id,
            "other_approval_id": other_approval.id,
            "event_id": event.id,
        }


def test_approvals_requeue_and_activities(api: APIContext) -> None:
    headers = api.auth()
    other_headers = api.auth("other")
    ids = api.run(_seed_approval_activity(api.session_factory, api.user_id, api.other_user_id))
    decided = api.client.post(
        f"/api/v1/approvals/{ids['approval_id']}/approve",
        headers=headers,
        json={"reason": "确认写入测试文件"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["approval"]["status"] == "approved"
    assert decided.json()["task"]["status"] == "queued"
    assert decided.json()["approval"]["payload_json"]["decision_reason"] == "确认写入测试文件"
    repeated = api.client.post(f"/api/v1/approvals/{ids['approval_id']}/approve", headers=headers)
    assert repeated.status_code == 409
    assert (
        api.client.get(f"/api/v1/approvals/{ids['approval_id']}", headers=other_headers).status_code
        == 404
    )

    activities = api.client.get("/api/v1/activities", headers=headers)
    assert activities.status_code == 200
    assert activities.json()
    assert all(item["type"] != "private" for item in activities.json())
    activity = api.client.get(f"/api/v1/activities/{ids['event_id']}", headers=headers)
    assert activity.status_code == 200
    assert activity.json()["type"] == "task.waiting"
    artifacts = api.client.get(
        "/api/v1/activities/artifacts", headers=headers, params={"task_id": ids["task_id"]}
    )
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["name"] == "report.txt"

    async def add_pending_approval(*, expired: bool = False) -> str:
        async with api.session_factory() as session:
            delta = timedelta(minutes=-1 if expired else 5)
            approval = Approval(
                user_id=api.user_id,
                task_id=ids["task_id"],
                action="executor.write",
                expires_at=datetime.now(UTC).replace(tzinfo=None) + delta,
            )
            session.add(approval)
            await session.commit()
            return approval.id

    rejected_id = api.run(add_pending_approval())
    rejected = api.client.post(f"/api/v1/approvals/{rejected_id}/reject", headers=headers, json={})
    assert rejected.status_code == 200
    assert rejected.json()["approval"]["status"] == "rejected"
    assert rejected.json()["task"]["status"] == "queued"
    expired_id = api.run(add_pending_approval(expired=True))
    expired = api.client.post(f"/api/v1/approvals/{expired_id}/approve", headers=headers)
    assert expired.status_code == 409


def test_websocket_replay_live_transient_and_versioned_alias(api: APIContext) -> None:
    token = api.login()["access_token"]
    first = api.run(api.manager.events.emit(api.user_id, "task.first", {"n": 1}))
    second = api.run(api.manager.events.emit(api.user_id, "task.second", {"n": 2}))
    api.run(api.manager.events.emit(api.other_user_id, "private", {}))

    with api.client.websocket_connect(f"/ws/events?token={token}&after={first['id']}") as websocket:
        replayed = websocket.receive_json()
        assert replayed["id"] == second["id"]
        assert replayed["payload"] == {"n": 2}
        persisted = api.run(api.manager.events.emit(api.user_id, "task.persisted", {"ok": True}))
        while True:
            message = websocket.receive_json()
            if message.get("id") == persisted["id"]:
                assert message["type"] == "task.persisted"
                break
        api.run(api.manager.events.publish(api.user_id, "message.delta", {"text": "你"}))
        delta = websocket.receive_json()
        assert delta["id"] is None
        assert delta["type"] == "message.delta"
        assert delta["payload"] == {"text": "你"}

    with api.client.websocket_connect(f"/api/v1/ws/events?token={token}&after=0") as websocket:
        assert websocket.receive_json()["id"] == first["id"]

    with pytest.raises(WebSocketDisconnect) as denied:
        with api.client.websocket_connect("/ws/events?token=invalid-token&after=0"):
            pass
    assert denied.value.code == 4401
