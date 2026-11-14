"""Task inspection, state changes, cancellation, and retry endpoints."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Goal, Task, User
from ...schemas import TaskOut, TaskStatusUpdate, TaskUpdate
from ..deps import get_current_user, get_db, get_owned_or_404, get_task_manager

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _set_task_status(task: Task, new_status: str) -> None:
    task.status = new_status
    if new_status == "queued":
        task.lease_until = None
        task.heartbeat_at = None
        task.started_at = None
        task.finished_at = None
        task.error = None
    elif new_status in {"succeeded", "failed", "cancelled"}:
        task.finished_at = datetime.now(UTC).replace(tzinfo=None)
        task.lease_until = None
        if new_status == "succeeded":
            task.progress = 100.0


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    task_status: str | None = Query(default=None, alias="status"),
    goal_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Task]:
    statement = select(Task).where(Task.user_id == current_user.id)
    if task_status is not None:
        statement = statement.where(Task.status == task_status)
    if goal_id is not None:
        await get_owned_or_404(db, Goal, goal_id, current_user.id, resource="Goal")
        statement = statement.where(Task.goal_id == goal_id)
    result = await db.execute(
        statement.order_by(Task.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Task:
    return await get_owned_or_404(db, Task, task_id, current_user.id, resource="Task")


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Task:
    task = await get_owned_or_404(db, Task, task_id, current_user.id, resource="Task")
    changes = payload.model_dump(exclude_unset=True)
    new_status = changes.pop("status", None)
    for key, value in changes.items():
        setattr(task, key, value)
    if new_status is not None:
        if task.status == "running":
            raise HTTPException(
                status_code=409, detail="Stop the running task before editing status"
            )
        _set_task_status(task, new_status)
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/status", response_model=TaskOut)
async def set_task_status(
    task_id: str,
    payload: TaskStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Task:
    task = await get_owned_or_404(db, Task, task_id, current_user.id, resource="Task")
    if task.status == "running":
        raise HTTPException(status_code=409, detail="Use cancel to stop a running task")
    _set_task_status(task, payload.status)
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    task_manager: Any = Depends(get_task_manager),
) -> Task:
    task = await get_owned_or_404(db, Task, task_id, current_user.id, resource="Task")
    try:
        await task_manager.cancel(task.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.refresh(task)
    return task


@router.post("/{task_id}/retry", response_model=TaskOut)
async def retry_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    task_manager: Any = Depends(get_task_manager),
) -> Task:
    task = await get_owned_or_404(db, Task, task_id, current_user.id, resource="Task")
    try:
        await task_manager.retry(task.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    task_manager: Any = Depends(get_task_manager),
) -> Response:
    task = await get_owned_or_404(db, Task, task_id, current_user.id, resource="Task")
    if task.status in {"queued", "running", "waiting_approval", "paused"}:
        await task_manager.cancel(task.id)
        await db.refresh(task)
    await db.execute(
        update(Task)
        .where(Task.parent_id == task.id, Task.user_id == current_user.id)
        .values(parent_id=None)
    )
    await db.delete(task)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
