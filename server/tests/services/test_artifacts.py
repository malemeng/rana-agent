import asyncio
from types import SimpleNamespace

from personal_agent.models import Artifact, Task, User
from personal_agent.services.tasks import TaskManager
from sqlalchemy import select


async def test_tool_artifacts_are_indexed(session_factory) -> None:
    async with session_factory() as session:
        user = User(username="artifacts", password_hash="test")
        session.add(user)
        await session.commit()
        user_id = user.id

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, tools=None, model=None):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    content="",
                    tool_calls=[
                        {
                            "id": "write-1",
                            "function": {
                                "name": "workspace_write",
                                "arguments": '{"path":"report.md","content":"done"}',
                            },
                        }
                    ],
                    usage={},
                )
            return SimpleNamespace(content="报告已生成", tool_calls=[], usage={})

    class Executor:
        async def execute(self, action, arguments, **kwargs):
            return {
                "ok": True,
                "data": {"path": "report.md", "size": 4},
                "artifacts": ["report.md"],
            }

    manager = TaskManager(session_factory, Provider(), Executor(), dispatch_interval=0.01)
    await manager.start()
    try:
        _, task = await manager.create_goal(user_id, "报告", "生成报告")
        for _ in range(200):
            async with session_factory() as session:
                current = await session.get(Task, task.id)
                if current.status == "succeeded":
                    break
            await asyncio.sleep(0.01)
        async with session_factory() as session:
            artifacts = (
                await session.scalars(select(Artifact).where(Artifact.task_id == task.id))
            ).all()
            assert len(artifacts) == 1
            assert artifacts[0].name == "report.md"
            assert artifacts[0].size == 4
    finally:
        await manager.stop()
        await manager.events.close()
