from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ChatResult:
    """Provider-neutral chat completion result used by the agent runtime."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


class ChatProvider(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> ChatResult: ...

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
    ) -> AsyncIterator[str]: ...
