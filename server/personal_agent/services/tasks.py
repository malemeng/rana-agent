from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Approval, Goal, Message, Task, utcnow
from .agent import AgentCancelled, AgentProvider, AgentRunner, ApprovalRequired
from .events import EventBus

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class TaskManager:
    """Persistent, leased task queue with per-user fair concurrent execution."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: AgentProvider,
        executor: Any = None,
        events: EventBus | None = None,
        *,
        settings: Any = None,
        agent_runner: AgentRunner | None = None,
        max_async_tasks: int | None = None,
        thread_workers: int | None = None,
        dispatch_interval: float | None = None,
        heartbeat_seconds: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.events = events or EventBus(session_factory)
        self.max_async_tasks = max(1, max_async_tasks or getattr(settings, "max_async_tasks", 4))
        self.thread_workers = max(1, thread_workers or getattr(settings, "max_thread_workers", 2))
        self.dispatch_interval = max(
            0.01,
            dispatch_interval
            if dispatch_interval is not None
            else getattr(settings, "dispatch_interval_ms", 200) / 1000,
        )
        self.heartbeat_seconds = max(
            0.05,
            heartbeat_seconds
            if heartbeat_seconds is not None
            else getattr(settings, "task_heartbeat_seconds", 10),
        )
        self.default_timeout_seconds = getattr(settings, "task_timeout_seconds", 600)
        self._thread_pool: ThreadPoolExecutor | None = None
        self.agent = agent_runner or AgentRunner(
            session_factory,
            provider,
            executor,
            self.events,
            max_steps=getattr(settings, "agent_max_steps", 8),
        )
        self._dispatcher: asyncio.Task[None] | None = None
        self._running: dict[str, asyncio.Task[None]] = {}
        self._running_users: dict[str, str] = {}
        self._cancel_requests: set[str] = set()
        self._dispatch_lock = asyncio.Lock()
        self._started = False
        self._stopping = False
        self._round_robin = 0

    def _ensure_thread_pool(self) -> ThreadPoolExecutor:
        if self._thread_pool is None:
            self._thread_pool = ThreadPoolExecutor(
                max_workers=self.thread_workers, thread_name_prefix="personal-agent"
            )
        return self._thread_pool

    async def start(self) -> None:
        if self._started:
            return
        self._stopping = False
        self._ensure_thread_pool()
        await self.recover()
        self._started = True
        self._dispatcher = asyncio.create_task(
            self._dispatch_loop(), name="personal-agent-task-dispatcher"
        )

    async def stop(self) -> None:
        if not self._started and not self._running:
            return
        self._stopping = True
        self._started = False
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
            self._dispatcher = None
        running = list(self._running.values())
        for execution in running:
            execution.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        if self._thread_pool is not None:
            self._thread_pool.shutdown(wait=False, cancel_futures=True)
            self._thread_pool = None

    async def recover(self) -> int:
        """Recover work left by a stopped process; approval waits remain untouched."""
        recovered: list[Task] = []
        now = utcnow()
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Task).where(Task.status.in_(["running", "cancelling"]))
                    )
                ).all()
            )
            for task in rows:
                task.lease_until = None
                task.heartbeat_at = None
                if not task.idempotent:
                    task.status = "failed"
                    task.error = "Non-idempotent task requires manual retry after runtime restart"
                    task.finished_at = now
                    if task.goal_id:
                        goal = await session.get(Goal, task.goal_id)
                        if goal is not None and goal.status == "active":
                            goal.status = "failed"
                            goal.error = task.error
                elif task.attempts < task.max_attempts:
                    task.status = "queued"
                    task.error = "Recovered after runtime restart"
                    task.started_at = None
                    task.finished_at = None
                    recovered.append(task)
                else:
                    task.status = "failed"
                    task.error = "Runtime stopped during final task attempt"
                    task.finished_at = now
                    if task.goal_id:
                        goal = await session.get(Goal, task.goal_id)
                        if goal is not None and goal.status == "active":
                            goal.status = "failed"
                            goal.error = task.error
            await session.commit()
        for task in recovered:
            await self.events.emit(
                task.user_id,
                "task.recovered",
                {"attempts": task.attempts},
                task_id=task.id,
                goal_id=task.goal_id,
            )
        return len(recovered)

    async def create_goal(
        self,
        user_id: str,
        title: str,
        input: str,
        priority: int = 50,
        idempotency_key: str | None = None,
    ) -> tuple[Goal, Task]:
        if idempotency_key:
            existing = await self._find_idempotent_goal(user_id, idempotency_key)
            if existing is not None:
                return existing
        goal = Goal(
            user_id=user_id,
            title=title,
            input=input,
            priority=priority,
            idempotency_key=idempotency_key,
        )
        task = Task(
            user_id=user_id,
            goal=goal,
            title=title,
            kind="agent",
            input_json={"title": title, "input": input},
            priority=priority,
            timeout_seconds=getattr(self, "default_timeout_seconds", 600),
            idempotency_key=idempotency_key,
        )
        async with self.session_factory() as session:
            session.add_all([goal, task])
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not idempotency_key:
                    raise
                existing = await self._find_idempotent_goal(user_id, idempotency_key)
                if existing is not None:
                    return existing
                raise
        await self.events.emit(
            user_id,
            "task.queued",
            {"title": title, "priority": priority},
            task_id=task.id,
            goal_id=goal.id,
        )
        return goal, task

    async def _find_idempotent_goal(
        self, user_id: str, idempotency_key: str
    ) -> tuple[Goal, Task] | None:
        async with self.session_factory() as session:
            goal = (
                await session.scalars(
                    select(Goal).where(
                        Goal.user_id == user_id,
                        Goal.idempotency_key == idempotency_key,
                    )
                )
            ).first()
            if goal is None:
                return None
            task = (await session.scalars(select(Task).where(Task.goal_id == goal.id))).first()
            return (goal, task) if task is not None else None

    async def create_conversation_task(
        self, user_id: str, conversation_id: str, message_id: str
    ) -> Task:
        idempotency_key = f"conversation-message:{message_id}"
        async with self.session_factory() as session:
            existing = (
                await session.scalars(
                    select(Task).where(
                        Task.user_id == user_id,
                        Task.idempotency_key == idempotency_key,
                    )
                )
            ).first()
            if existing is not None:
                return existing
            message = await session.get(Message, message_id)
            if (
                message is None
                or message.user_id != user_id
                or message.conversation_id != conversation_id
            ):
                raise ValueError("Message not found")
            task = Task(
                user_id=user_id,
                title=message.content[:200] or "新对话",
                kind="conversation",
                input_json={
                    "input": message.content,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                },
                priority=60,
                timeout_seconds=getattr(self, "default_timeout_seconds", 600),
                idempotency_key=idempotency_key,
            )
            session.add(task)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return (
                    await session.scalars(
                        select(Task).where(
                            Task.user_id == user_id,
                            Task.idempotency_key == idempotency_key,
                        )
                    )
                ).one()
        await self.events.emit(
            user_id,
            "task.queued",
            {"conversation_id": conversation_id, "message_id": message_id},
            task_id=task.id,
        )
        return task

    async def cancel(self, task_id: str) -> Task:
        self._cancel_requests.add(task_id)
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                self._cancel_requests.discard(task_id)
                raise ValueError("Task not found")
            event = None
            if task.status not in TERMINAL_STATUSES:
                task.status = "cancelled"
                task.lease_until = None
                task.heartbeat_at = None
                task.finished_at = utcnow()
                task.error = "Cancelled by user"
                if task.goal_id:
                    goal = await session.get(Goal, task.goal_id)
                    if goal is not None and goal.status == "active":
                        goal.status = "cancelled"
                event = await self.events.stage(
                    session,
                    task.user_id,
                    "task.cancelled",
                    {},
                    task_id=task_id,
                    goal_id=task.goal_id,
                )
            await session.commit()
        execution = self._running.get(task_id)
        if execution is not None:
            execution.cancel()
        if event is not None:
            await self.events.deliver(event)
        return task

    async def retry(self, task_id: str) -> Task:
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise ValueError("Task not found")
            if task.status in {"queued", "running", "cancelling"}:
                raise ValueError(f"Task is {task.status}")
            task.status = "queued"
            task.attempts = 0
            task.progress = 0.0
            task.error = None
            task.result_json = {}
            task.lease_until = None
            task.heartbeat_at = None
            task.started_at = None
            task.finished_at = None
            input_data = dict(task.input_json or {})
            input_data.pop("_approved", None)
            input_data.pop("_approval_decided", None)
            task.input_json = input_data
            if task.goal_id:
                goal = await session.get(Goal, task.goal_id)
                if goal is not None:
                    goal.status = "active"
                    goal.error = None
                    goal.completed_at = None
            await session.commit()
        self._cancel_requests.discard(task_id)
        await self.events.emit(
            task.user_id, "task.queued", {"retry": True}, task_id=task.id, goal_id=task.goal_id
        )
        return task

    async def resume_after_approval(self, task_id: str) -> Task:
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise ValueError("Task not found")
            if task.status == "queued":
                return task
            if task.status != "waiting_approval":
                raise ValueError(f"Task is {task.status}, not waiting for approval")
            approval = (
                await session.scalars(
                    select(Approval)
                    .where(
                        Approval.task_id == task_id,
                        Approval.status.in_(["approved", "rejected"]),
                    )
                    .order_by(Approval.decided_at.desc(), Approval.created_at.desc())
                    .limit(1)
                )
            ).first()
            if approval is None:
                raise ValueError("Approval has not been decided")
            approved = approval.status == "approved"
            task.status = "queued"
            task.error = None
            task.lease_until = None
            task.heartbeat_at = None
            task.started_at = None
            task.finished_at = None
            input_data = dict(task.input_json or {})
            input_data["_approved"] = approved
            input_data["_approval_decided"] = True
            task.input_json = input_data
            if task.goal_id:
                goal = await session.get(Goal, task.goal_id)
                if goal is not None:
                    goal.status = "active"
            await session.commit()
        self._cancel_requests.discard(task_id)
        await self.events.emit(
            task.user_id,
            "task.queued",
            {"approved": approved},
            task_id=task.id,
            goal_id=task.goal_id,
        )
        return task

    async def get(self, task_id: str) -> Task | None:
        async with self.session_factory() as session:
            return await session.get(Task, task_id)

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A transient database failure is retried on the next tick; queued rows persist.
                await asyncio.sleep(self.dispatch_interval)
            await asyncio.sleep(self.dispatch_interval)

    async def dispatch_once(self) -> int:
        if self._stopping:
            return 0
        async with self._dispatch_lock:
            available = self.max_async_tasks - len(self._running)
            if available <= 0:
                return 0
            candidates: list[Task] = []
            async with self.session_factory() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(Task)
                            .where(Task.status == "queued")
                            .order_by(Task.priority.desc(), Task.created_at, Task.id)
                            .limit(max(100, self.max_async_tasks * 20))
                        )
                    ).all()
                )
                if not rows:
                    return 0
                running_counts = {
                    user_id: count
                    for user_id, count in (
                        await session.execute(
                            select(Task.user_id, func.count(Task.id))
                            .where(Task.status == "running")
                            .group_by(Task.user_id)
                        )
                    ).all()
                }
                groups: dict[str, list[Task]] = defaultdict(list)
                for row in rows:
                    groups[row.user_id].append(row)
                users = list(groups)
                start = self._round_robin % len(users)
                users = users[start:] + users[:start]
                self._round_robin += 1
                fair_limit = max(1, math.ceil(self.max_async_tasks / len(users)))
                while len(candidates) < available:
                    added = False
                    for user_id in users:
                        if len(candidates) >= available:
                            break
                        selected_for_user = sum(item.user_id == user_id for item in candidates)
                        if (
                            groups[user_id]
                            and running_counts.get(user_id, 0) + selected_for_user < fair_limit
                        ):
                            candidates.append(groups[user_id].pop(0))
                            added = True
                    if not added:
                        break

                claimed: list[Task] = []
                now = utcnow()
                lease = now + timedelta(seconds=max(30.0, self.heartbeat_seconds * 3))
                for candidate in candidates:
                    result = await session.execute(
                        update(Task)
                        .where(Task.id == candidate.id, Task.status == "queued")
                        .values(
                            status="running",
                            attempts=Task.attempts + 1,
                            started_at=now,
                            heartbeat_at=now,
                            lease_until=lease,
                            finished_at=None,
                            error=None,
                        )
                    )
                    if getattr(result, "rowcount", 0) != 1:
                        continue
                    await session.commit()
                    task = await session.get(Task, candidate.id)
                    if task is not None:
                        claimed.append(task)

            self._ensure_thread_pool()
            for task in claimed:
                execution = asyncio.create_task(
                    self._execute(task.id), name=f"personal-agent-task-{task.id}"
                )
                self._running[task.id] = execution
                self._running_users[task.id] = task.user_id
                await self.events.emit(
                    task.user_id,
                    "task.started",
                    {"attempt": task.attempts},
                    task_id=task.id,
                    goal_id=task.goal_id,
                )
            return len(claimed)

    async def _is_cancelled(self, task_id: str) -> bool:
        if task_id in self._cancel_requests:
            return True
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            return task is None or task.status == "cancelled"

    async def _heartbeat(self, task_id: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            now = utcnow()
            async with self.session_factory() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id, Task.status == "running")
                    .values(
                        heartbeat_at=now,
                        lease_until=now + timedelta(seconds=max(30.0, self.heartbeat_seconds * 3)),
                    )
                )
                await session.commit()

    async def _execute(self, task_id: str) -> None:
        heartbeat: asyncio.Task[None] | None = None
        try:
            async with self.session_factory() as session:
                task = await session.get(Task, task_id)
                if task is None or task.status != "running":
                    return
                timeout = task.timeout_seconds
                stream = task.kind == "conversation"
                run_task = Task(
                    id=task.id,
                    user_id=task.user_id,
                    goal_id=task.goal_id,
                    title=task.title,
                    kind=task.kind,
                    input_json=dict(task.input_json or {}),
                    result_json=dict(task.result_json or {}),
                )
            heartbeat = asyncio.create_task(
                self._heartbeat(task_id), name=f"personal-agent-heartbeat-{task_id}"
            )
            result = await asyncio.wait_for(
                self.agent.run(
                    run_task,
                    cancel_check=lambda: self._is_cancelled(task_id),
                    stream=stream,
                    thread_pool=self._thread_pool,
                ),
                timeout=timeout,
            )
            await self._succeed(task_id, result)
        except ApprovalRequired:
            # AgentRunner committed the approval and its exact continuation checkpoint.
            pass
        except TimeoutError:
            await self._fail(task_id, f"Task exceeded its {timeout} second timeout")
        except (AgentCancelled, asyncio.CancelledError):
            if self._stopping and task_id not in self._cancel_requests:
                await self._requeue_interrupted(task_id)
            else:
                await self._mark_cancelled(task_id)
        except Exception as exc:
            await self._fail(task_id, f"{type(exc).__name__}: {exc}")
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            self._running.pop(task_id, None)
            self._running_users.pop(task_id, None)

    async def _succeed(self, task_id: str, result: dict[str, Any]) -> None:
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None or task.status != "running":
                return
            task.status = "succeeded"
            task.progress = 1.0
            task.result_json = result
            task.error = None
            task.lease_until = None
            task.heartbeat_at = None
            task.finished_at = utcnow()
            if task.goal_id:
                goal = await session.get(Goal, task.goal_id)
                if goal is not None:
                    remaining = await session.scalar(
                        select(func.count(Task.id)).where(
                            Task.goal_id == goal.id,
                            Task.id != task.id,
                            Task.status.not_in(TERMINAL_STATUSES),
                        )
                    )
                    if remaining:
                        goal.status = "active"
                        goal.completed_at = None
                    else:
                        goal.status = "completed"
                        goal.result = str(result.get("content", ""))
                        goal.completed_at = task.finished_at
                        goal.error = None
            user_id = task.user_id
            goal_id = task.goal_id
            event = await self.events.stage(
                session,
                user_id,
                "task.succeeded",
                {"result": result},
                task_id=task_id,
                goal_id=goal_id,
            )
            await session.commit()
        self._cancel_requests.discard(task_id)
        await self.events.deliver(event)

    async def _fail(self, task_id: str, error: str) -> None:
        event_type = "task.failed"
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            if (
                task is None
                or task.status in TERMINAL_STATUSES
                or task.status == "waiting_approval"
            ):
                return
            task.error = error
            task.lease_until = None
            task.heartbeat_at = None
            if task.idempotent and task.attempts < task.max_attempts:
                task.status = "queued"
                task.started_at = None
                event_type = "task.retry_scheduled"
            else:
                task.status = "failed"
                task.finished_at = utcnow()
                if task.goal_id:
                    goal = await session.get(Goal, task.goal_id)
                    if goal is not None:
                        goal.status = "failed"
                        goal.error = error
            user_id = task.user_id
            goal_id = task.goal_id
            status = task.status
            attempts = task.attempts
            event = await self.events.stage(
                session,
                user_id,
                event_type,
                {"error": error, "status": status, "attempts": attempts},
                task_id=task_id,
                goal_id=goal_id,
            )
            await session.commit()
        await self.events.deliver(event)

    async def _mark_cancelled(self, task_id: str) -> None:
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            event = None
            if task.status != "cancelled":
                task.status = "cancelled"
                task.error = "Cancelled"
                task.finished_at = utcnow()
                event = await self.events.stage(
                    session,
                    task.user_id,
                    "task.cancelled",
                    {},
                    task_id=task_id,
                    goal_id=task.goal_id,
                )
            task.lease_until = None
            task.heartbeat_at = None
            if task.goal_id:
                goal = await session.get(Goal, task.goal_id)
                if goal is not None and goal.status == "active":
                    goal.status = "cancelled"
            await session.commit()
        if event is not None:
            await self.events.deliver(event)

    async def _requeue_interrupted(self, task_id: str) -> None:
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None or task.status in TERMINAL_STATUSES:
                return
            if not task.idempotent:
                task.status = "failed"
                task.error = "Non-idempotent task interrupted by runtime shutdown"
                task.finished_at = utcnow()
                if task.goal_id:
                    goal = await session.get(Goal, task.goal_id)
                    if goal is not None:
                        goal.status = "failed"
                        goal.error = task.error
            else:
                task.status = "queued"
                task.error = "Interrupted by runtime shutdown"
                task.started_at = None
            task.lease_until = None
            task.heartbeat_at = None
            await session.commit()

    async def wait_idle(self, timeout: float = 10.0) -> None:
        """Wait until no queued/running database work or local executions remain."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            async with self.session_factory() as session:
                pending = await session.scalar(
                    select(func.count(Task.id)).where(Task.status.in_(["queued", "running"]))
                )
            if not pending and not self._running:
                return
            await asyncio.sleep(min(self.dispatch_interval, 0.05))
        raise TimeoutError("Tasks did not become idle before timeout")
