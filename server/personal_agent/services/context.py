from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, cast, or_, select, text
from sqlalchemy.exc import DatabaseError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Agent, Conversation, Memory, Message

_FTS_STATEMENTS = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
    USING fts5(content, content='memories', content_rowid='rowid')
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_fts_insert AFTER INSERT ON memories BEGIN
      INSERT INTO memory_fts(rowid, content) VALUES (new.rowid, new.content);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
      INSERT INTO memory_fts(memory_fts, rowid, content)
      VALUES ('delete', old.rowid, old.content);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_fts_update AFTER UPDATE OF content ON memories BEGIN
      INSERT INTO memory_fts(memory_fts, rowid, content)
      VALUES ('delete', old.rowid, old.content);
      INSERT INTO memory_fts(rowid, content) VALUES (new.rowid, new.content);
    END
    """,
)


def _fts_query(query: str) -> str | None:
    words = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
    # Quoting each token makes arbitrary user input safe for the FTS query grammar.
    return " OR ".join(f'"{word.replace(chr(34), chr(34) * 2)}"' for word in words[:16]) or None


def _like_query(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_terms(query: str) -> list[str]:
    terms: list[str] = []
    for word in re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:16]:
        terms.append(word)
        if len(word) > 2 and re.search(r"[\u3400-\u9fff]", word):
            # SQLite's default unicode61 tokenizer treats a Chinese sentence as one token.
            # Bigrams provide useful substring recall without requiring an external segmenter.
            terms.extend(word[index : index + 2] for index in range(len(word) - 1))
    return list(dict.fromkeys(terms))


class MemoryRepository:
    """Memory CRUD and search, using SQLite FTS5 with a portable LIKE fallback."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self._fts_available: bool | None = None

    async def _ensure_fts(self, session: AsyncSession) -> bool:
        if self._fts_available is False:
            return False
        bind = session.get_bind()
        if bind.dialect.name != "sqlite":
            self._fts_available = False
            return False
        try:
            for statement in _FTS_STATEMENTS:
                await session.execute(text(statement))
            # Backfill rows created before the triggers were installed. This is idempotent.
            await session.execute(text("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')"))
            await session.commit()
            self._fts_available = True
            return True
        except (OperationalError, DatabaseError):
            await session.rollback()
            self._fts_available = False
            return False

    async def add(
        self,
        user_id: str,
        content: str,
        *,
        kind: str = "candidate",
        tags: list[str] | None = None,
        pinned: bool = False,
        confidence: float = 1.0,
        source_message_id: str | None = None,
    ) -> Memory:
        async with self.session_factory() as session:
            await self._ensure_fts(session)
            memory = Memory(
                user_id=user_id,
                content=content,
                kind=kind,
                tags=tags or [],
                pinned=pinned,
                confidence=confidence,
                source_message_id=source_message_id,
            )
            session.add(memory)
            await session.commit()
            return memory

    async def update(self, memory_id: str, **changes: Any) -> Memory | None:
        allowed = {"content", "kind", "tags", "pinned", "archived", "confidence"}
        async with self.session_factory() as session:
            memory = await session.get(Memory, memory_id)
            if memory is None:
                return None
            for key, value in changes.items():
                if key in allowed:
                    setattr(memory, key, value)
            await session.commit()
            return memory

    async def search(
        self,
        user_id: str,
        query: str = "",
        *,
        limit: int = 8,
        kinds: list[str] | None = None,
        include_archived: bool = False,
    ) -> list[Memory]:
        limit = max(1, min(limit, 100))
        async with self.session_factory() as session:
            fts_query = _fts_query(query)
            if fts_query and await self._ensure_fts(session):
                fts_statement = text(
                    """
                    SELECT memories.id
                    FROM memory_fts JOIN memories ON memories.rowid = memory_fts.rowid
                    WHERE memory_fts MATCH :fts_query AND memories.user_id = :user_id
                      AND (:include_archived = 1 OR memories.archived = 0)
                    ORDER BY memories.pinned DESC, memories.confidence DESC,
                             memories.updated_at DESC
                    LIMIT :limit
                    """
                )
                try:
                    ids: list[str] = list(
                        (
                            await session.execute(
                                fts_statement,
                                {
                                    "fts_query": fts_query,
                                    "user_id": user_id,
                                    "include_archived": int(include_archived),
                                    "limit": limit,
                                },
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if ids:
                        memories = (
                            await session.scalars(select(Memory).where(Memory.id.in_(ids)))
                        ).all()
                        by_id = {memory.id: memory for memory in memories}
                        rows = [by_id[item] for item in ids if item in by_id]
                        if kinds is not None:
                            rows = [row for row in rows if row.kind in kinds]
                        if rows:
                            return rows[:limit]
                    # FTS tokenization may not match substrings (notably Chinese text).
                    # Fall through to LIKE when the FTS index has no useful hit.
                except (OperationalError, DatabaseError):
                    await session.rollback()
                    self._fts_available = False

            conditions = [Memory.user_id == user_id]
            if not include_archived:
                conditions.append(Memory.archived.is_(False))
            if kinds is not None:
                conditions.append(Memory.kind.in_(kinds))
            words = _like_terms(query)
            if words:
                text_conditions = []
                for word in words:
                    pattern = f"%{_like_query(word)}%"
                    text_conditions.append(
                        or_(
                            Memory.content.like(pattern, escape="\\"),
                            cast(Memory.tags, String).like(pattern, escape="\\"),
                        )
                    )
                conditions.append(or_(*text_conditions))
            memory_statement = (
                select(Memory)
                .where(*conditions)
                .order_by(Memory.pinned.desc(), Memory.confidence.desc(), Memory.updated_at.desc())
                .limit(limit)
            )
            return list((await session.scalars(memory_statement)).all())


@dataclass(slots=True)
class AgentContext:
    messages: list[dict[str, Any]]
    model: str | None = None
    memories: list[Memory] | None = None


class ContextBuilder:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        memories: MemoryRepository | None = None,
        *,
        message_limit: int = 40,
        memory_limit: int = 8,
    ) -> None:
        self.session_factory = session_factory
        self.memories = memories or MemoryRepository(session_factory)
        self.message_limit = message_limit
        self.memory_limit = memory_limit

    async def build(
        self,
        user_id: str,
        input_text: str,
        *,
        conversation_id: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> AgentContext:
        history: list[Message] = []
        summary = ""
        async with self.session_factory() as session:
            conversation = None
            if conversation_id:
                conversation = await session.get(Conversation, conversation_id)
                if conversation is None or conversation.user_id != user_id:
                    raise ValueError("Conversation not found")
            agent = None
            if conversation is not None and conversation.agent_id:
                agent = await session.get(Agent, conversation.agent_id)
            if agent is None:
                agent = (
                    await session.scalars(
                        select(Agent)
                        .where(Agent.user_id == user_id)
                        .order_by(Agent.created_at)
                        .limit(1)
                    )
                ).first()
            if conversation is not None:
                summary = conversation.summary
                history = list(
                    (
                        await session.scalars(
                            select(Message)
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.created_at.desc(), Message.id.desc())
                            .limit(self.message_limit)
                        )
                    ).all()
                )
                history.reverse()
            prompt = system_prompt or (
                agent.system_prompt if agent else "你是一个可靠、持续工作的个人助理。"
            )
            selected_model = model or (agent.model if agent else None)

        relevant_memories = await self.memories.search(user_id, input_text, limit=self.memory_limit)
        messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
        if summary:
            messages.append({"role": "system", "content": f"对话摘要：\n{summary}"})
        if relevant_memories:
            memory_text = "\n".join(f"- {memory.content}" for memory in relevant_memories)
            messages.append({"role": "system", "content": f"相关长期记忆：\n{memory_text}"})
        messages.extend(
            {"role": message.role, "content": message.content}
            for message in history
            if message.role in {"system", "user", "assistant"}
        )
        if not conversation_id:
            messages.append({"role": "user", "content": input_text})
        return AgentContext(messages=messages, model=selected_model, memories=relevant_memories)

    async def build_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return (await self.build(*args, **kwargs)).messages


MemoryService = MemoryRepository
