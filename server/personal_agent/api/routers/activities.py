"""Read-only task event and artifact activity feeds."""

from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Artifact, Task, TaskEvent, User
from ...schemas import ActivityOut, ArtifactOut
from ..deps import get_current_user, get_db, get_owned_or_404

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", response_model=list[ActivityOut])
async def list_activities(
    event_type: str | None = Query(default=None, alias="type"),
    task_id: str | None = None,
    goal_id: str | None = None,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TaskEvent]:
    statement = select(TaskEvent).where(
        TaskEvent.user_id == current_user.id,
        TaskEvent.id > after,
    )
    if event_type is not None:
        statement = statement.where(TaskEvent.type == event_type)
    if task_id is not None:
        statement = statement.where(TaskEvent.task_id == task_id)
    if goal_id is not None:
        statement = statement.where(TaskEvent.goal_id == goal_id)
    result = await db.execute(statement.order_by(TaskEvent.id.desc()).limit(limit).offset(offset))
    return list(result.scalars().all())


@router.get("/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(
    task_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Artifact]:
    statement = select(Artifact).where(Artifact.user_id == current_user.id)
    if task_id is not None:
        await get_owned_or_404(db, Task, task_id, current_user.id, resource="Task")
        statement = statement.where(Artifact.task_id == task_id)
    result = await db.execute(
        statement.order_by(Artifact.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    artifact = await get_owned_or_404(
        db, Artifact, artifact_id, current_user.id, resource="Artifact"
    )
    executor = getattr(request.app.state, "executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Executor is not configured")
    path = quote(artifact.path, safe="/")
    try:
        response = await executor.client.get(f"/api/v1/artifacts/{artifact.task_id}/{path}")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Artifact download failed") from exc
    return Response(
        content=response.content,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": (f"attachment; filename*=UTF-8''{quote(artifact.name)}"),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{activity_id}", response_model=ActivityOut)
async def get_activity(
    activity_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskEvent:
    result = await db.execute(
        select(TaskEvent).where(
            TaskEvent.id == activity_id,
            TaskEvent.user_id == current_user.id,
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    return event
