import asyncio

from personal_agent.models import Goal, Task, User
from personal_agent.services.tasks import TaskManager
from sqlalchemy import select


async def test_ten_goals_thirty_tasks_respect_concurrency(session_factory) -> None:
    async with session_factory() as session:
        user = User(username="load", password_hash="test")
        session.add(user)
        await session.flush()
        goals = [
            Goal(user_id=user.id, title=f"Goal {index}", input="并发压力测试")
            for index in range(10)
        ]
        session.add_all(goals)
        await session.flush()
        for goal in goals:
            for task_index in range(3):
                session.add(
                    Task(
                        user_id=user.id,
                        goal_id=goal.id,
                        title=f"{goal.title} / Task {task_index}",
                        input_json={"input": "并发压力测试"},
                    )
                )
        await session.commit()

    class Provider:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def complete(self, messages, tools=None, model=None):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.03)
                return {"content": "done", "tool_calls": [], "usage": {}}
            finally:
                self.active -= 1

    provider = Provider()
    manager = TaskManager(
        session_factory,
        provider,
        max_async_tasks=4,
        thread_workers=2,
        dispatch_interval=0.005,
    )
    await manager.start()
    try:
        await asyncio.wait_for(manager.wait_idle(), timeout=20)
        async with session_factory() as session:
            tasks = (await session.scalars(select(Task))).all()
            assert len(tasks) == 30
            assert {task.status for task in tasks} == {"succeeded"}
            stored_goals = (await session.scalars(select(Goal))).all()
            assert {goal.status for goal in stored_goals} == {"completed"}
        assert 2 <= provider.max_active <= 4
    finally:
        await manager.stop()
        await manager.events.close()
