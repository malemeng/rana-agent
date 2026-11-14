"""Conversation and message endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Agent, Conversation, Message, Task, User
from ...schemas import (
    ConversationCreate,
    ConversationOut,
    ConversationTaskResponse,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
)
from ..deps import get_current_user, get_db, get_owned_or_404, get_task_manager

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _validate_agent(db: AsyncSession, agent_id: str | None, user_id: str) -> None:
    if agent_id is None:
        return
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    await _validate_agent(db, payload.agent_id, current_user.id)
    conversation = Conversation(user_id=current_user.id, **payload.model_dump())
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    return await get_owned_or_404(
        db, Conversation, conversation_id, current_user.id, resource="Conversation"
    )


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    conversation = await get_owned_or_404(
        db, Conversation, conversation_id, current_user.id, resource="Conversation"
    )
    changes = payload.model_dump(exclude_unset=True)
    await _validate_agent(db, changes.get("agent_id"), current_user.id)
    for key, value in changes.items():
        setattr(conversation, key, value)
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    conversation = await get_owned_or_404(
        db, Conversation, conversation_id, current_user.id, resource="Conversation"
    )
    await db.delete(conversation)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Message]:
    await get_owned_or_404(
        db, Conversation, conversation_id, current_user.id, resource="Conversation"
    )
    result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.user_id == current_user.id,
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_message(
    conversation_id: str,
    payload: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    task_manager: Any = Depends(get_task_manager),
) -> ConversationTaskResponse:
    await get_owned_or_404(
        db, Conversation, conversation_id, current_user.id, resource="Conversation"
    )
    message = Message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        role="user",
        content=payload.content,
        status="completed",
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    task: Task = await task_manager.create_conversation_task(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message.id,
    )
    return ConversationTaskResponse.model_validate(
        {"message": MessageOut.model_validate(message), "task": task}
    )
