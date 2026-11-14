"""Persistent cron schedule CRUD."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Schedule, User
from ...schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate
from ..deps import get_current_user, get_db, get_owned_or_404

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _validate_schedule(cron: str, timezone: str) -> None:
    if not croniter.is_valid(cron):
        raise HTTPException(status_code=422, detail="Invalid cron expression")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Invalid IANA timezone") from exc


def _next_run(cron: str, timezone: str) -> datetime:
    local_now = datetime.now(ZoneInfo(timezone))
    next_local = croniter(cron, local_now).get_next(datetime)
    return next_local.astimezone(UTC).replace(tzinfo=None)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    enabled: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Schedule]:
    statement = select(Schedule).where(Schedule.user_id == current_user.id)
    if enabled is not None:
        statement = statement.where(Schedule.enabled == enabled)
    result = await db.execute(
        statement.order_by(Schedule.next_run_at.asc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Schedule:
    _validate_schedule(payload.cron, payload.timezone)
    values = payload.model_dump(exclude={"next_run_at"})
    next_run_at = (
        _normalize_datetime(payload.next_run_at)
        if payload.next_run_at is not None
        else _next_run(payload.cron, payload.timezone)
    )
    schedule = Schedule(user_id=current_user.id, next_run_at=next_run_at, **values)
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(
    schedule_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Schedule:
    return await get_owned_or_404(db, Schedule, schedule_id, current_user.id, resource="Schedule")


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Schedule:
    schedule = await get_owned_or_404(
        db, Schedule, schedule_id, current_user.id, resource="Schedule"
    )
    changes = payload.model_dump(exclude_unset=True)
    cron = changes.get("cron", schedule.cron)
    timezone = changes.get("timezone", schedule.timezone)
    _validate_schedule(cron, timezone)
    supplied_next_run = changes.pop("next_run_at", None)
    for key, value in changes.items():
        setattr(schedule, key, value)
    if supplied_next_run is not None:
        schedule.next_run_at = _normalize_datetime(supplied_next_run)
    elif "next_run_at" in payload.model_fields_set or {"cron", "timezone"} & changes.keys():
        schedule.next_run_at = _next_run(cron, timezone)
    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    schedule = await get_owned_or_404(
        db, Schedule, schedule_id, current_user.id, resource="Schedule"
    )
    await db.delete(schedule)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
