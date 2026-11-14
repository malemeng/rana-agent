"""Authenticated persistent and transient task-event stream over WebSocket."""

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import Any

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..config import get_settings
from ..models import TaskEvent, User
from ..security import decode_access_token

router = APIRouter()
_POLL_SECONDS = 0.5
_HEARTBEAT_SECONDS = 20.0
_BATCH_SIZE = 200


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


@router.websocket("/ws/events")
async def stream_events(
    websocket: WebSocket,
    token: str = Query(min_length=10),
    after: int = Query(default=0, ge=0),
) -> None:
    """Replay persisted events, then deliver live events with persistent recovery."""
    settings = getattr(websocket.app.state, "settings", None) or get_settings()
    session_factory = getattr(websocket.app.state, "session_factory", None)
    if not settings.secret_key or session_factory is None:
        await websocket.close(code=1011, reason="Server is not configured")
        return
    try:
        claims = decode_access_token(token, secret_key=settings.secret_key)
    except (jwt.InvalidTokenError, ValueError):
        await websocket.close(code=4401, reason="Invalid or expired access token")
        return

    async with session_factory() as db:
        user = await db.get(User, claims["sub"])
    if user is None:
        await websocket.close(code=4401, reason="User no longer exists")
        return

    manager = getattr(websocket.app.state, "task_manager", None)
    event_service = getattr(manager, "events", None)
    await websocket.accept()
    last_id = after
    last_heartbeat = time.monotonic()
    receive_task = asyncio.create_task(websocket.receive())
    subscription = event_service.subscribe(user.id) if event_service is not None else None
    live_task = asyncio.create_task(anext(subscription)) if subscription is not None else None
    if live_task is not None:
        # Let the broker register before replay starts, closing the replay/live gap.
        await asyncio.sleep(0)

    async def stored_events() -> list[dict[str, Any]]:
        if event_service is not None:
            return await event_service.list(user.id, after_id=last_id, limit=_BATCH_SIZE)
        async with session_factory() as db:
            result = await db.execute(
                select(TaskEvent)
                .where(TaskEvent.user_id == user.id, TaskEvent.id > last_id)
                .order_by(TaskEvent.id.asc())
                .limit(_BATCH_SIZE)
            )
            rows = result.scalars().all()
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

    try:
        while True:
            if receive_task.done():
                message = receive_task.result()
                if message["type"] == "websocket.disconnect":
                    break
                receive_task = asyncio.create_task(websocket.receive())

            # The database cursor is authoritative. It also recovers persistent
            # events if a slow client's bounded transient queue drops an item.
            events = await stored_events()
            for event in events:
                event_id = event.get("id")
                if isinstance(event_id, int) and event_id <= last_id:
                    continue
                await websocket.send_json(event)
                if isinstance(event_id, int):
                    last_id = event_id

            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                await websocket.send_json(
                    {
                        "type": "ping",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "after": last_id,
                    }
                )
                last_heartbeat = now
            if len(events) >= _BATCH_SIZE:
                continue

            wait_for = {receive_task}
            if live_task is not None:
                wait_for.add(live_task)
            done, _ = await asyncio.wait(
                wait_for,
                timeout=_POLL_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if live_task is not None and live_task in done:
                try:
                    live_event = live_task.result()
                except StopAsyncIteration:
                    break
                assert subscription is not None
                live_task = asyncio.create_task(anext(subscription))
                event_id = live_event.get("id")
                if not isinstance(event_id, int):
                    # Only transient events bypass the ordered database cursor.
                    # Persistent live notifications simply trigger an immediate
                    # replay, avoiding duplicates and out-of-order concurrent commits.
                    await websocket.send_json(live_event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receive_task.cancel()
        if live_task is not None:
            live_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receive_task
        if live_task is not None:
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await live_task
        if subscription is not None:
            with contextlib.suppress(RuntimeError):
                await subscription.aclose()
