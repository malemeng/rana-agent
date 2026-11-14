"""Human approval decisions and task requeue integration."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Approval, Task, User
from ...schemas import ApprovalDecision, ApprovalOut, ApprovalTaskResponse
from ..deps import get_current_user, get_db, get_owned_or_404, get_task_manager

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    approval_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Approval]:
    statement = select(Approval).where(Approval.user_id == current_user.id)
    if approval_status is not None:
        statement = statement.where(Approval.status == approval_status)
    result = await db.execute(
        statement.order_by(Approval.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Approval:
    return await get_owned_or_404(db, Approval, approval_id, current_user.id, resource="Approval")


async def _decide(
    approval_id: str,
    decision: str,
    payload: ApprovalDecision | None,
    current_user: User,
    db: AsyncSession,
    task_manager: Any,
) -> ApprovalTaskResponse:
    approval = await get_owned_or_404(
        db, Approval, approval_id, current_user.id, resource="Approval"
    )
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail="Approval has already been decided")
    now = datetime.now(UTC).replace(tzinfo=None)
    if approval.expires_at <= now:
        approval.status = "expired"
        await db.commit()
        raise HTTPException(status_code=409, detail="Approval has expired")
    task = await get_owned_or_404(db, Task, approval.task_id, current_user.id, resource="Task")
    if task.status not in {"waiting_approval", "queued"}:
        raise HTTPException(status_code=409, detail="Task is not waiting for approval")
    approval.status = decision
    approval.decided_at = now
    if payload is not None and payload.reason:
        approval.payload_json = {**approval.payload_json, "decision_reason": payload.reason}
    await db.commit()

    # TaskManager reads the persisted decision and performs the actual queue
    # transition for both approvals and rejections.
    await task_manager.resume_after_approval(task.id)
    await db.refresh(approval)
    await db.refresh(task)
    return ApprovalTaskResponse.model_validate({"approval": approval, "task": task})


@router.post("/{approval_id}/approve", response_model=ApprovalTaskResponse)
async def approve(
    approval_id: str,
    payload: ApprovalDecision | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    task_manager: Any = Depends(get_task_manager),
) -> ApprovalTaskResponse:
    return await _decide(approval_id, "approved", payload, current_user, db, task_manager)


@router.post("/{approval_id}/reject", response_model=ApprovalTaskResponse)
async def reject(
    approval_id: str,
    payload: ApprovalDecision | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    task_manager: Any = Depends(get_task_manager),
) -> ApprovalTaskResponse:
    return await _decide(approval_id, "rejected", payload, current_user, db, task_manager)
