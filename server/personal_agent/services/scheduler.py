from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Schedule, Task
from .events import EventBus
from .tasks import TaskManager


def _aware_utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _storage_time(value: datetime) -> datetime:
    return _aware_utc(value).replace(tzinfo=None)


def next_cron_time(
    expression: str,
    timezone: str = "Asia/Shanghai",
    *,
    after: datetime | None = None,
) -> datetime:
    """Return a UTC-naive value suitable for the existing Schedule model."""
    if not croniter.is_valid(expression):
        raise ValueError("Invalid cron expression")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone}") from exc
    base = _aware_utc(after).astimezone(zone)
    following = croniter(expression, base).get_next(datetime)
    if following.tzinfo is None:
        following = following.replace(tzinfo=zone)
    return _storage_time(following)


class TaskScheduler:
    """Persistent cron scheduler with coalesced catch-up and idempotent task creation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        task_manager: TaskManager,
        events: EventBus | None = None,
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        self.session_factory = session_factory
        self.task_manager = task_manager
        self.events = events or task_manager.events
        self.interval_seconds = max(0.05, interval_seconds)
        self._worker: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._stopping = False

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._stopping = False
        self._worker = asyncio.create_task(self._run(), name="personal-agent-scheduler")

    async def stop(self) -> None:
        self._stopping = True
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def _run(self) -> None:
        while True:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Schedules remain durable; the next tick retries transient database errors.
                pass
            await asyncio.sleep(self.interval_seconds)

    async def create_schedule(
        self,
        user_id: str,
        title: str,
        prompt: str,
        cron: str,
        timezone: str = "Asia/Shanghai",
        enabled: bool = True,
        *,
        next_run_at: datetime | None = None,
    ) -> Schedule:
        calculated = next_run_at or next_cron_time(cron, timezone)
        # Validate cron/timezone even when an explicit first run is supplied.
        next_cron_time(cron, timezone, after=calculated)
        async with self.session_factory() as session:
            schedule = Schedule(
                user_id=user_id,
                title=title,
                prompt=prompt,
                cron=cron,
                timezone=timezone,
                enabled=enabled,
                next_run_at=_storage_time(calculated),
            )
            session.add(schedule)
            await session.commit()
            return schedule

    async def list_schedules(self, user_id: str) -> list[Schedule]:
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(Schedule)
                        .where(Schedule.user_id == user_id)
                        .order_by(Schedule.created_at)
                    )
                ).all()
            )

    async def update_schedule(
        self, schedule_id: str, user_id: str | None = None, **changes: Any
    ) -> Schedule | None:
        allowed = {"title", "prompt", "cron", "timezone", "enabled", "next_run_at"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown schedule fields: {', '.join(sorted(unknown))}")
        async with self.session_factory() as session:
            schedule = await session.get(Schedule, schedule_id)
            if schedule is None or (user_id is not None and schedule.user_id != user_id):
                return None
            for key, value in changes.items():
                if key == "next_run_at" and value is not None:
                    value = _storage_time(value)
                setattr(schedule, key, value)
            if {"cron", "timezone"} & set(changes) and "next_run_at" not in changes:
                schedule.next_run_at = next_cron_time(schedule.cron, schedule.timezone)
            elif "next_run_at" in changes:
                next_cron_time(schedule.cron, schedule.timezone, after=schedule.next_run_at)
            await session.commit()
            return schedule

    async def set_enabled(
        self, schedule_id: str, enabled: bool, user_id: str | None = None
    ) -> Schedule | None:
        async with self.session_factory() as session:
            schedule = await session.get(Schedule, schedule_id)
            if schedule is None or (user_id is not None and schedule.user_id != user_id):
                return None
            schedule.enabled = enabled
            if enabled:
                schedule.next_run_at = next_cron_time(schedule.cron, schedule.timezone)
            await session.commit()
            return schedule

    async def delete_schedule(self, schedule_id: str, user_id: str | None = None) -> bool:
        async with self.session_factory() as session:
            schedule = await session.get(Schedule, schedule_id)
            if schedule is None or (user_id is not None and schedule.user_id != user_id):
                return False
            await session.delete(schedule)
            await session.commit()
            return True

    async def run_due(self, *, now: datetime | None = None, limit: int = 100) -> list[Task]:
        if self._stopping:
            return []
        async with self._lock:
            current = _storage_time(now or datetime.now(UTC))
            async with self.session_factory() as session:
                due = list(
                    (
                        await session.scalars(
                            select(Schedule)
                            .where(
                                Schedule.enabled.is_(True),
                                Schedule.next_run_at <= current,
                            )
                            .order_by(Schedule.next_run_at)
                            .limit(max(1, min(limit, 1000)))
                        )
                    ).all()
                )
                claimed: list[tuple[str, str, str, str, str, datetime]] = []
                for schedule in due:
                    scheduled_for = schedule.next_run_at
                    # Coalesce missed runs after downtime into one task and schedule the next
                    # occurrence strictly after the current instant.
                    following = next_cron_time(schedule.cron, schedule.timezone, after=current)
                    result = await session.execute(
                        update(Schedule)
                        .where(
                            Schedule.id == schedule.id,
                            Schedule.enabled.is_(True),
                            Schedule.next_run_at == scheduled_for,
                        )
                        .values(last_run_at=current, next_run_at=following)
                    )
                    if getattr(result, "rowcount", 0) == 1:
                        claimed.append(
                            (
                                schedule.id,
                                schedule.user_id,
                                schedule.title,
                                schedule.prompt,
                                schedule.cron,
                                scheduled_for,
                            )
                        )
                    await session.commit()

            tasks: list[Task] = []
            for schedule_id, user_id, title, prompt, _, scheduled_for in claimed:
                idempotency_key = (
                    f"schedule:{schedule_id}:{int(scheduled_for.replace(tzinfo=UTC).timestamp())}"
                )
                try:
                    _, task = await self.task_manager.create_goal(
                        user_id,
                        title,
                        prompt,
                        idempotency_key=idempotency_key,
                    )
                    tasks.append(task)
                    await self.events.emit(
                        user_id,
                        "schedule.triggered",
                        {"schedule_id": schedule_id, "scheduled_for": scheduled_for.isoformat()},
                        task_id=task.id,
                        goal_id=task.goal_id,
                    )
                except Exception as exc:
                    await self.events.emit(
                        user_id,
                        "schedule.failed",
                        {"schedule_id": schedule_id, "error": f"{type(exc).__name__}: {exc}"},
                    )
            return tasks


Scheduler = TaskScheduler
