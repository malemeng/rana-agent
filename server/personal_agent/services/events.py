from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import TaskEvent


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


@dataclass(slots=True)
class _Subscriber:
    queue: asyncio.Queue[dict[str, Any] | None]
    task_id: str | None = None
    goal_id: str | None = None

    def wants(self, event: Mapping[str, Any]) -> bool:
        return (self.task_id is None or self.task_id == event.get("task_id")) and (
            self.goal_id is None or self.goal_id == event.get("goal_id")
        )


class TransientEventBroker:
    """In-process fan-out for events that must not survive a reconnect."""

    def __init__(self, *, queue_size: int = 256) -> None:
        self.queue_size = queue_size
        self._subscribers: dict[str, dict[str, _Subscriber]] = defaultdict(dict)
        self._lock = asyncio.Lock()
        self._closed = False

    async def subscribe(
        self,
        user_id: str,
        *,
        task_id: str | None = None,
        goal_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        subscription_id = uuid.uuid4().hex
        subscriber = _Subscriber(asyncio.Queue(maxsize=self.queue_size), task_id, goal_id)
        async with self._lock:
            if self._closed:
                return
            self._subscribers[user_id][subscription_id] = subscriber
        try:
            while True:
                event = await subscriber.queue.get()
                if event is None:
                    return
                yield event
        finally:
            async with self._lock:
                users = self._subscribers.get(user_id)
                if users is not None:
                    users.pop(subscription_id, None)
                    if not users:
                        self._subscribers.pop(user_id, None)

    async def publish(self, user_id: str, event: Mapping[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.get(user_id, {}).values())
        for subscriber in subscribers:
            if not subscriber.wants(event):
                continue
            if subscriber.queue.full():
                # A slow websocket must not block task execution. Keep the newest events.
                try:
                    subscriber.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                subscriber.queue.put_nowait(dict(event))
            except asyncio.QueueFull:
                pass

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            subscribers = [
                subscriber
                for user_subscribers in self._subscribers.values()
                for subscriber in user_subscribers.values()
            ]
            self._subscribers.clear()
        for subscriber in subscribers:
            if subscriber.queue.full():
                try:
                    subscriber.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                subscriber.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass


class EventBus:
    """Persistent task activity with optional transient delivery to live subscribers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        broker: TransientEventBroker | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.broker = broker or TransientEventBroker()

    def subscribe(
        self,
        user_id: str,
        *,
        task_id: str | None = None,
        goal_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        return self.broker.subscribe(user_id, task_id=task_id, goal_id=goal_id)

    @staticmethod
    def _event(
        user_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None,
        task_id: str | None,
        goal_id: str | None,
    ) -> dict[str, Any]:
        return {
            "id": None,
            "user_id": user_id,
            "goal_id": goal_id,
            "task_id": task_id,
            "type": event_type,
            "payload": dict(payload or {}),
            "created_at": _iso(datetime.now(UTC)),
        }

    async def stage(
        self,
        session: AsyncSession,
        user_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        """Stage an event in the caller's transaction; call deliver after its commit."""
        event = self._event(user_id, event_type, payload, task_id, goal_id)
        row = TaskEvent(
            user_id=user_id,
            goal_id=goal_id,
            task_id=task_id,
            type=event_type,
            payload_json=event["payload"],
        )
        session.add(row)
        await session.flush()
        event["id"] = row.id
        event["created_at"] = _iso(row.created_at)
        return event

    async def deliver(self, event: Mapping[str, Any]) -> None:
        await self.broker.publish(str(event["user_id"]), event)

    async def emit(
        self,
        user_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
        goal_id: str | None = None,
        persistent: bool = True,
    ) -> dict[str, Any]:
        if persistent:
            async with self.session_factory() as session:
                event = await self.stage(
                    session,
                    user_id,
                    event_type,
                    payload,
                    task_id=task_id,
                    goal_id=goal_id,
                )
                await session.commit()
        else:
            event = self._event(user_id, event_type, payload, task_id, goal_id)
        await self.deliver(event)
        return event

    async def publish(
        self,
        user_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.emit(
            user_id,
            event_type,
            payload,
            task_id=task_id,
            goal_id=goal_id,
            persistent=False,
        )

    async def list(
        self,
        user_id: str,
        *,
        task_id: str | None = None,
        goal_id: str | None = None,
        after_id: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        async with self.session_factory() as session:
            statement = select(TaskEvent).where(TaskEvent.user_id == user_id)
            if task_id is not None:
                statement = statement.where(TaskEvent.task_id == task_id)
            if goal_id is not None:
                statement = statement.where(TaskEvent.goal_id == goal_id)
            if after_id is not None:
                statement = statement.where(TaskEvent.id > after_id)
            rows = (await session.scalars(statement.order_by(TaskEvent.id).limit(limit))).all()
            return [
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "goal_id": row.goal_id,
                    "task_id": row.task_id,
                    "type": row.type,
                    "payload": row.payload_json,
                    "created_at": _iso(row.created_at),
                }
                for row in rows
            ]

    async def close(self) -> None:
        await self.broker.close()


# Explicit alias for callers that prefer to distinguish persistence from transport.
EventService = EventBus
EventBroker = TransientEventBroker
