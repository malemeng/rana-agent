"""Persistent goal endpoints; creation is delegated to the task manager."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Goal, User
from ...schemas import GoalCreate, GoalOut, GoalStatusUpdate, GoalTaskResponse, GoalUpdate
from ..deps import get_current_user, get_db, get_owned_or_404, get_task_manager

router = APIRouter(prefix="/goals", tags=["goals"])


@router.get("", response_model=list[GoalOut])
async def list_goals(
    goal_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Goal]:
    statement = select(Goal).where(Goal.user_id == current_user.id)
    if goal_status is not None:
        statement = statement.where(Goal.status == goal_status)
    result = await db.execute(
        statement.order_by(Goal.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.post("", response_model=GoalTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(
    payload: GoalCreate,
    current_user: User = Depends(get_current_user),
    task_manager: Any = Depends(get_task_manager),
) -> GoalTaskResponse:
    goal, task = await task_manager.create_goal(
        user_id=current_user.id,
        title=payload.title,
        input=payload.input,
        priority=payload.priority,
        idempotency_key=payload.idempotency_key,
    )
    return GoalTaskResponse.model_validate({"goal": goal, "task": task})


@router.get("/{goal_id}", response_model=GoalOut)
async def get_goal(
    goal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Goal:
    return await get_owned_or_404(db, Goal, goal_id, current_user.id, resource="Goal")


@router.patch("/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: str,
    payload: GoalUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Goal:
    goal = await get_owned_or_404(db, Goal, goal_id, current_user.id, resource="Goal")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(goal, key, value)
    if changes.get("status") == "completed" and goal.completed_at is None:
        goal.completed_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    await db.refresh(goal)
    return goal


@router.post("/{goal_id}/status", response_model=GoalOut)
async def set_goal_status(
    goal_id: str,
    payload: GoalStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Goal:
    goal = await get_owned_or_404(db, Goal, goal_id, current_user.id, resource="Goal")
    goal.status = payload.status
    if payload.status == "completed" and goal.completed_at is None:
        goal.completed_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    await db.refresh(goal)
    return goal


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    goal = await get_owned_or_404(db, Goal, goal_id, current_user.id, resource="Goal")
    await db.delete(goal)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
