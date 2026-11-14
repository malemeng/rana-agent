import personal_agent.connectors.service as module
import pytest
from personal_agent.connectors.base import ConnectorError
from personal_agent.connectors.service import ConnectorToolExecutor
from personal_agent.models import Connection, Goal, Task, User
from personal_agent.security import hash_password


async def _make_task(session_factory, user_id: str) -> str:
    async with session_factory() as session:
        goal = Goal(user_id=user_id, title="Read", input="Read")
        session.add(goal)
        await session.flush()
        task = Task(user_id=user_id, goal_id=goal.id, title="Read")
        session.add(task)
        await session.commit()
        return task.id


async def test_connector_is_owner_scoped(session_factory, user_id: str) -> None:
    task_id = await _make_task(session_factory, user_id)
    async with session_factory() as session:
        other = User(username="other", password_hash=hash_password("password"))
        session.add(other)
        await session.flush()
        connection = Connection(
            user_id=other.id,
            kind="rss",
            name="private feed",
            config_json={"url": "https://feed.local/rss"},
        )
        session.add(connection)
        await session.commit()
        connection_id = connection.id

    executor = ConnectorToolExecutor(session_factory, None)
    with pytest.raises(ConnectorError, match="不存在"):
        await executor._connector(task_id, connection_id)


async def test_rss_connector_success(session_factory, user_id: str) -> None:
    class FeedConnector:
        def __init__(self, url: str):
            self.url = url

        async def read(self, **kwargs):
            return {"items": [{"title": self.url}], "count": 1}

    original = module.RSSConnector
    module.RSSConnector = FeedConnector
    try:
        task_id = await _make_task(session_factory, user_id)
        async with session_factory() as session:
            connection = Connection(
                user_id=user_id, kind="rss", name="feed", config_json={"url": "ok"}
            )
            session.add(connection)
            await session.commit()
            connection_id = connection.id
        result = await ConnectorToolExecutor(session_factory, None).execute(
            "rss_read", {"connection_id": connection_id}, task_id=task_id
        )
        assert result["data"]["count"] == 1
    finally:
        module.RSSConnector = original
