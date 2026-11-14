from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from personal_agent.models import (
    Agent,
    Approval,
    Conversation,
    Goal,
    Message,
    Schedule,
    Task,
    User,
)
from personal_agent.services.context import ContextBuilder, MemoryRepository
from personal_agent.services.events import EventBus
from personal_agent.services.executor import ExecutorClient
from personal_agent.services.scheduler import TaskScheduler, next_cron_time
from personal_agent.services.tasks import TaskManager
from sqlalchemy import select


class FakeProvider:
    def __init__(self, responses: list[Any] | None = None, chunks: list[str] | None = None):
        self.responses = list(responses or [])
        self.chunks = chunks or ["你", "好"]
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages, tools=None, model=None):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model})
        response = self.responses.pop(0) if self.responses else {"content": "完成"}
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response()
        if isinstance(response, dict):
            return SimpleNamespace(**response)
        return response

    async def stream_chat(self, messages, model=None):
        self.calls.append({"messages": messages, "model": model, "stream": True})
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk


class FakeExecutor:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def execute(self, action, arguments, task_id=None, approved=False):
        self.calls.append(
            {
                "action": action,
                "arguments": arguments,
                "task_id": task_id,
                "approved": approved,
            }
        )
        if not approved:
            return {"status": "approval_required"}
        return {"status": "succeeded", "value": arguments["value"] * 2}


async def make_user(session_factory, username="owner") -> User:
    async with session_factory() as session:
        user = User(username=username, password_hash="test")
        session.add(user)
        await session.commit()
        return user


async def wait_status(session_factory, task_id: str, *statuses: str, timeout: float = 5) -> Task:
    async def poll() -> Task:
        while True:
            async with session_factory() as session:
                task = await session.get(Task, task_id)
                if task is not None and task.status in statuses:
                    return task
            await asyncio.sleep(0.01)

    try:
        return await asyncio.wait_for(poll(), timeout)
    except TimeoutError as exc:
        async with session_factory() as session:
            task = await session.get(Task, task_id)
            detail = None if task is None else (task.status, task.error, task.attempts)
        raise AssertionError(f"Task did not reach {statuses}: {detail}") from exc


async def test_goal_idempotency_execution_and_events(session_factory):
    user = await make_user(session_factory)
    provider = FakeProvider([{"content": "目标完成", "usage": {"total_tokens": 7}}])
    manager = TaskManager(
        session_factory,
        provider,
        max_async_tasks=2,
        thread_workers=1,
        dispatch_interval=0.01,
    )
    await manager.start()
    try:
        goal, task = await manager.create_goal(
            user.id, "整理", "整理今天的计划", idempotency_key="same-request"
        )
        same_goal, same_task = await manager.create_goal(
            user.id, "整理", "整理今天的计划", idempotency_key="same-request"
        )
        assert same_goal.id == goal.id
        assert same_task.id == task.id
        done = await wait_status(session_factory, task.id, "succeeded")
        assert done.result_json["content"] == "目标完成"
        assert done.result_json["usage"]["total_tokens"] == 7
        assert len(provider.calls) == 1
        async with session_factory() as session:
            stored_goal = await session.get(Goal, goal.id)
            assert stored_goal.status == "completed"
            assert stored_goal.result == "目标完成"
        events = await manager.events.list(user.id, task_id=task.id)
        event_types = {event["type"] for event in events}
        assert {"task.queued", "task.started", "agent.step", "task.succeeded"} <= event_types
    finally:
        await manager.stop()
        await manager.events.close()


async def test_conversation_stream_persists_assistant_message(session_factory):
    user = await make_user(session_factory)
    async with session_factory() as session:
        conversation = Conversation(user_id=user.id)
        session.add(conversation)
        await session.flush()
        message = Message(
            user_id=user.id,
            conversation_id=conversation.id,
            role="user",
            content="你好",
        )
        session.add(message)
        await session.commit()
        conversation_id = conversation.id
        message_id = message.id
    provider = FakeProvider(chunks=["你", "好", "呀"])
    manager = TaskManager(session_factory, provider, dispatch_interval=0.01)
    await manager.start()
    try:
        task = await manager.create_conversation_task(user.id, conversation_id, message_id)
        duplicate = await manager.create_conversation_task(user.id, conversation_id, message_id)
        assert duplicate.id == task.id
        done = await wait_status(session_factory, task.id, "succeeded")
        assert done.result_json == {
            "content": "你好呀",
            "steps": 1,
            "usage": {},
            "streamed": True,
        }
        async with session_factory() as session:
            assistant = (
                await session.scalars(
                    select(Message).where(Message.turn_id == task.id, Message.role == "assistant")
                )
            ).one()
            assert assistant.content == "你好呀"
        events = await manager.events.list(user.id, task_id=task.id)
        assert all(event["type"] != "message.delta" for event in events)
    finally:
        await manager.stop()
        await manager.events.close()


async def test_tool_loop_waits_and_resumes_at_approved_call(session_factory):
    user = await make_user(session_factory)
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "calculate", "arguments": json.dumps({"value": 21})},
    }
    provider = FakeProvider(
        [
            {"content": "先计算", "tool_calls": [tool_call]},
            {"content": "结果是 42"},
        ]
    )
    executor = FakeExecutor()
    manager = TaskManager(session_factory, provider, executor, dispatch_interval=0.01)
    await manager.start()
    try:
        _, task = await manager.create_goal(user.id, "计算", "把 21 乘以 2")
        async with session_factory() as session:
            assert await session.get(Task, task.id) is not None
        waiting = await wait_status(session_factory, task.id, "waiting_approval")
        checkpoint = waiting.result_json["_agent"]
        assert checkpoint["pending_tools"][0]["id"] == "call-1"
        async with session_factory() as session:
            approval = (
                await session.scalars(select(Approval).where(Approval.task_id == task.id))
            ).one()
            assert approval.action == "calculate"
            approval.status = "approved"
            approval.decided_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
        await manager.resume_after_approval(task.id)
        done = await wait_status(session_factory, task.id, "succeeded")
        assert done.result_json["content"] == "结果是 42"
        assert [call["approved"] for call in executor.calls] == [False, True]
        assert len(provider.calls) == 2
        tool_messages = [
            message
            for call in provider.calls
            for message in call["messages"]
            if message["role"] == "tool"
        ]
        assert len(tool_messages) == 1
        assert "42" in tool_messages[0]["content"]
    finally:
        await manager.stop()
        await manager.events.close()


async def test_rejected_tool_returns_decision_to_model_without_execution(session_factory):
    user = await make_user(session_factory)
    provider = FakeProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "delete-1",
                        "function": {"name": "workspace_delete", "arguments": {"path": "a"}},
                    }
                ]
            },
            {"content": "已取消删除"},
        ]
    )
    executor = FakeExecutor()
    manager = TaskManager(session_factory, provider, executor, dispatch_interval=0.01)
    await manager.start()
    try:
        _, task = await manager.create_goal(user.id, "删除", "删除文件")
        await wait_status(session_factory, task.id, "waiting_approval")
        async with session_factory() as session:
            approval = (
                await session.scalars(select(Approval).where(Approval.task_id == task.id))
            ).one()
            approval.status = "rejected"
            approval.decided_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
        await manager.resume_after_approval(task.id)
        done = await wait_status(session_factory, task.id, "succeeded")
        assert done.result_json["content"] == "已取消删除"
        assert len(executor.calls) == 1
        tool_message = provider.calls[-1]["messages"][-1]
        assert tool_message["role"] == "tool"
        assert "rejected" in tool_message["content"]
    finally:
        await manager.stop()
        await manager.events.close()


async def test_goal_registers_default_workspace_tools_and_executes_write(session_factory):
    user = await make_user(session_factory)
    provider = FakeProvider(
        [
            {
                "content": "写入文件",
                "tool_calls": [
                    {
                        "id": "write-1",
                        "function": {
                            "name": "workspace_write",
                            "arguments": {"path": "notes/today.md", "content": "完成"},
                        },
                    }
                ],
            },
            {"content": "文件已写入"},
        ]
    )

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, action, arguments, **kwargs):
            self.calls.append((action, arguments, kwargs))
            return {"ok": True, "path": arguments["path"]}

    executor = RecordingExecutor()
    manager = TaskManager(session_factory, provider, executor, dispatch_interval=0.01)
    await manager.start()
    try:
        _, task = await manager.create_goal(user.id, "写文件", "记录今天")
        done = await wait_status(session_factory, task.id, "succeeded")
        assert done.result_json["content"] == "文件已写入"
        assert executor.calls[0][0] == "workspace_write"
        tool_names = {tool["function"]["name"] for tool in provider.calls[0]["tools"]}
        assert {
            "workspace_list",
            "workspace_read",
            "workspace_write",
            "workspace_delete",
            "shell",
            "browser",
            "caldav_read",
            "imap_read",
            "rss_read",
        } <= tool_names
    finally:
        await manager.stop()
        await manager.events.close()


async def test_automatic_retry_and_manual_retry_after_failure(session_factory):
    user = await make_user(session_factory)
    provider = FakeProvider([RuntimeError("temporary"), {"content": "第二次成功"}])
    manager = TaskManager(session_factory, provider, dispatch_interval=0.01)
    await manager.start()
    try:
        _, task = await manager.create_goal(user.id, "重试", "测试自动重试")
        done = await wait_status(session_factory, task.id, "succeeded")
        assert done.attempts == 2
        provider.responses.append(RuntimeError("permanent"))
        await manager.retry(task.id)
        done = await wait_status(session_factory, task.id, "succeeded", "failed")
        # One remaining response is consumed; subsequent attempts use the provider default.
        assert done.status == "succeeded"
        assert done.attempts >= 2
    finally:
        await manager.stop()
        await manager.events.close()


async def test_cancel_and_shutdown_recovery(session_factory):
    user = await make_user(session_factory)
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingProvider:
        async def complete(self, messages, tools=None, model=None):
            started.set()
            await release.wait()
            return SimpleNamespace(content="late", tool_calls=[], usage={})

        async def stream_chat(self, messages, model=None):
            yield "unused"

    manager = TaskManager(session_factory, BlockingProvider(), dispatch_interval=0.01)
    await manager.start()
    _, first = await manager.create_goal(user.id, "取消", "等待")
    await asyncio.wait_for(started.wait(), 2)
    cancelled = await manager.cancel(first.id)
    assert cancelled.status == "cancelled"
    await manager.wait_idle()

    _, second = await manager.create_goal(user.id, "停止", "等待")
    await wait_status(session_factory, second.id, "running")
    await manager.stop()
    async with session_factory() as session:
        interrupted = await session.get(Task, second.id)
        assert interrupted.status == "queued"
    assert await manager.recover() == 0
    await manager.events.close()


async def test_recover_requeues_running_and_fails_exhausted_task(session_factory):
    user = await make_user(session_factory)
    async with session_factory() as session:
        recoverable = Task(user_id=user.id, title="recover", status="running", attempts=1)
        exhausted = Task(
            user_id=user.id,
            title="exhausted",
            status="running",
            attempts=3,
            max_attempts=3,
        )
        non_idempotent = Task(
            user_id=user.id,
            title="non-idempotent",
            status="running",
            attempts=1,
            idempotent=False,
        )
        session.add_all([recoverable, exhausted, non_idempotent])
        await session.commit()
        ids = [recoverable.id, exhausted.id, non_idempotent.id]
    manager = TaskManager(session_factory, FakeProvider())
    assert await manager.recover() == 1
    async with session_factory() as session:
        assert (await session.get(Task, ids[0])).status == "queued"
        assert (await session.get(Task, ids[1])).status == "failed"
        assert (await session.get(Task, ids[2])).status == "failed"
    await manager.events.close()


async def test_heartbeat_and_timeout_mark_task_failed(session_factory):
    user = await make_user(session_factory)
    started = asyncio.Event()

    class SlowProvider:
        async def complete(self, messages, tools=None, model=None):
            started.set()
            await asyncio.sleep(10)
            return SimpleNamespace(content="late", tool_calls=[], usage={})

    manager = TaskManager(
        session_factory,
        SlowProvider(),
        dispatch_interval=0.01,
        heartbeat_seconds=0.05,
    )
    _, task = await manager.create_goal(user.id, "超时", "持续运行")
    async with session_factory() as session:
        row = await session.get(Task, task.id)
        row.timeout_seconds = 0.2
        row.max_attempts = 1
        await session.commit()
    await manager.start()
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.sleep(0.08)
        async with session_factory() as session:
            beating = await session.get(Task, task.id)
            assert beating.heartbeat_at is not None
            assert beating.lease_until is not None
        failed = await wait_status(session_factory, task.id, "failed", timeout=2)
        assert "timeout" in failed.error
        assert failed.attempts == 1
    finally:
        await manager.stop()
        await manager.events.close()


async def test_fair_dispatch_uses_separate_users(session_factory):
    first_user = await make_user(session_factory, "first")
    second_user = await make_user(session_factory, "second")
    release = asyncio.Event()
    entered = asyncio.Event()

    class ConcurrentProvider:
        def __init__(self):
            self.count = 0

        async def complete(self, messages, tools=None, model=None):
            self.count += 1
            if self.count == 2:
                entered.set()
            await release.wait()
            return SimpleNamespace(content="done", tool_calls=[], usage={})

    provider = ConcurrentProvider()
    manager = TaskManager(
        session_factory,
        provider,
        max_async_tasks=2,
        dispatch_interval=10,
    )
    await manager.start()
    try:
        # Pause the automatic dispatcher so this test explicitly exercises one fair batch.
        manager._dispatcher.cancel()
        await asyncio.gather(manager._dispatcher, return_exceptions=True)
        _, first = await manager.create_goal(first_user.id, "一", "一")
        _, second = await manager.create_goal(second_user.id, "二", "二")
        assert await manager.dispatch_once() == 2
        await asyncio.wait_for(entered.wait(), 2)
        assert {manager._running_users[first.id], manager._running_users[second.id]} == {
            first_user.id,
            second_user.id,
        }
        release.set()
        await manager.wait_idle()
    finally:
        release.set()
        await manager.stop()
        await manager.events.close()


async def test_memory_fts_like_fallback_and_context(session_factory):
    user = await make_user(session_factory)
    other = await make_user(session_factory, "other")
    repository = MemoryRepository(session_factory)
    await repository.add(user.id, "likes green tea and hiking", tags=["preference"])
    await repository.add(user.id, "周末喜欢去西湖散步", kind="profile")
    await repository.add(other.id, "private other-user memory")
    found = await repository.search(user.id, "hiking")
    assert [memory.content for memory in found] == ["likes green tea and hiking"]
    chinese = await repository.search(user.id, "西湖")
    assert [memory.content for memory in chinese] == ["周末喜欢去西湖散步"]
    assert await repository.search(user.id, "other-user") == []
    async with session_factory() as session:
        session.add(Agent(user_id=user.id, system_prompt="系统提示", model="fake-model"))
        await session.commit()
    context = await ContextBuilder(session_factory, repository).build(user.id, "西湖怎么走")
    assert context.model == "fake-model"
    assert context.messages[0]["content"] == "系统提示"
    assert any("西湖散步" in message["content"] for message in context.messages)


async def test_event_broker_transient_delivery_and_persistent_replay(session_factory):
    user = await make_user(session_factory)
    bus = EventBus(session_factory)
    received = []

    async def collect():
        async for event in bus.subscribe(user.id, task_id="task-1"):
            received.append(event)
            return

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await bus.emit(
        user.id,
        "token",
        {"value": "x"},
        task_id="task-1",
        persistent=False,
    )
    await asyncio.wait_for(collector, 2)
    await bus.emit(user.id, "stored", {"value": 1}, task_id="task-1")
    assert received[0]["payload"] == {"value": "x"}
    replay = await bus.list(user.id, task_id="task-1")
    assert [event["type"] for event in replay] == ["stored"]
    await bus.close()


async def test_persistent_scheduler_coalesces_and_deduplicates(session_factory):
    user = await make_user(session_factory)
    manager = TaskManager(session_factory, FakeProvider())
    scheduler = TaskScheduler(session_factory, manager, interval_seconds=10)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    schedule = await scheduler.create_schedule(
        user.id,
        "日程",
        "执行日程",
        "0 * * * *",
        timezone="UTC",
        next_run_at=now - timedelta(hours=3),
    )
    tasks = await scheduler.run_due(now=now)
    assert len(tasks) == 1
    assert await scheduler.run_due(now=now) == []
    async with session_factory() as session:
        stored = await session.get(Schedule, schedule.id)
        assert stored.last_run_at == now.replace(tzinfo=None)
        assert stored.next_run_at == datetime(2026, 1, 1, 13, 0)
    # A new scheduler instance sees the same durable state and does not fire again.
    assert await TaskScheduler(session_factory, manager).run_due(now=now) == []
    following = next_cron_time("*/15 * * * *", "Asia/Shanghai", after=now)
    assert following.minute in {0, 15, 30, 45}
    with pytest.raises(ValueError):
        await scheduler.create_schedule(user.id, "bad", "bad", "not a cron")
    await manager.events.close()


async def test_executor_client_contract_without_network():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "succeeded", "result": 3})

    http_client = httpx.AsyncClient(
        base_url="http://executor.test", transport=httpx.MockTransport(handler)
    )
    client = ExecutorClient(
        "http://executor.test",
        "secret",
        client=http_client,
        max_retries=0,
    )
    result = await client.execute("sum", {"a": 1, "b": 2}, task_id="task-1")
    assert result["result"] == 3
    request = requests[0]
    assert request.url.path == "/api/v1/execute"
    assert json.loads(request.content) == {
        "tool": "sum",
        "arguments": {"a": 1, "b": 2},
        "task_id": "task-1",
        "timeout_seconds": 120.0,
    }
    assert request.headers["Authorization"] == "Bearer secret"
    await client.aclose()
    await http_client.aclose()
