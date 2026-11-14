"""Owner-scoped long-term memory CRUD."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Memory, Message, User
from ...schemas import MemoryCreate, MemoryOut, MemoryUpdate
from ..deps import get_current_user, get_db, get_owned_or_404

router = APIRouter(prefix="/memories", tags=["memories"])


async def _validate_source(db: AsyncSession, message_id: str | None, user_id: str) -> None:
    if message_id is None:
        return
    result = await db.execute(
        select(Message).where(Message.id == message_id, Message.user_id == user_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Source message not found")


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    kind: str | None = None,
    archived: bool | None = None,
    pinned: bool | None = None,
    q: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Memory]:
    statement = select(Memory).where(Memory.user_id == current_user.id)
    if kind is not None:
        statement = statement.where(Memory.kind == kind)
    if archived is not None:
        statement = statement.where(Memory.archived == archived)
    if pinned is not None:
        statement = statement.where(Memory.pinned == pinned)
    if q:
        statement = statement.where(Memory.content.contains(q, autoescape=True))
    result = await db.execute(
        statement.order_by(Memory.pinned.desc(), Memory.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Memory:
    await _validate_source(db, payload.source_message_id, current_user.id)
    memory = Memory(user_id=current_user.id, **payload.model_dump())
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return memory


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Memory:
    return await get_owned_or_404(db, Memory, memory_id, current_user.id, resource="Memory")


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: str,
    payload: MemoryUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Memory:
    memory = await get_owned_or_404(db, Memory, memory_id, current_user.id, resource="Memory")
    changes = payload.model_dump(exclude_unset=True)
    await _validate_source(db, changes.get("source_message_id"), current_user.id)
    for key, value in changes.items():
        setattr(memory, key, value)
    await db.commit()
    await db.refresh(memory)
    return memory


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    memory = await get_owned_or_404(db, Memory, memory_id, current_user.id, resource="Memory")
    await db.delete(memory)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
