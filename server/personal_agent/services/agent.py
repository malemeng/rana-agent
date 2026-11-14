from __future__ import annotations

import asyncio
import functools
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Approval, Artifact, Message, Task, utcnow
from .context import AgentContext, ContextBuilder
from .events import EventBus


@runtime_checkable
class AgentProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> Any: ...

    def stream_chat(
        self, messages: list[dict[str, Any]], model: str | None = None
    ) -> AsyncIterator[str]: ...


class AgentCancelled(RuntimeError):
    pass


class ApprovalRequired(RuntimeError):
    def __init__(self, task_id: str, approval_id: str) -> None:
        super().__init__("Tool execution requires approval")
        self.task_id = task_id
        self.approval_id = approval_id


def default_agent_tools() -> list[dict[str, Any]]:
    """Tools available to goal executions unless the caller explicitly overrides them."""

    def schema(
        name: str, description: str, properties: dict[str, Any], required: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required or [],
                },
            },
        }

    path = {"path": {"type": "string", "description": "Workspace-relative path"}}
    tools = [
        schema("workspace_list", "List files in the task workspace.", path),
        schema("workspace_read", "Read a UTF-8 workspace file.", path, ["path"]),
        schema(
            "workspace_write",
            "Write a UTF-8 workspace file.",
            {**path, "content": {"type": "string"}},
            ["path", "content"],
        ),
        schema("workspace_delete", "Delete a workspace file.", path, ["path"]),
        schema(
            "shell",
            "Run a command in the isolated task workspace.",
            {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
            },
            ["command"],
        ),
        schema(
            "browser",
            "Operate the sandbox browser.",
            {
                "action": {
                    "type": "string",
                    "enum": ["open", "read", "click", "fill", "screenshot"],
                },
                "url": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
            },
            ["action"],
        ),
    ]
    try:
        from ..connectors.service import CONNECTOR_TOOL_SCHEMAS
    except ImportError:
        pass
    else:
        tools.extend(CONNECTOR_TOOL_SCHEMAS)
    return tools


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(vars(value)) if hasattr(value, "__dict__") else {}


def _tool_call(value: Any) -> dict[str, Any]:
    raw = _as_dict(value)
    function = raw.get("function")
    if function is None and hasattr(value, "function"):
        function = value.function
    function_data = _as_dict(function)
    name = function_data.get("name") or raw.get("name")
    arguments = function_data.get("arguments", raw.get("arguments", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"input": arguments}
    if not isinstance(arguments, dict):
        arguments = {"input": arguments}
    return {
        "id": str(raw.get("id") or f"call_{uuid.uuid4().hex}"),
        "name": str(name or ""),
        "arguments": arguments,
    }


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class AgentRunner:
    """Bounded model/tool loop with optional token streaming and durable approval checkpoints."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: AgentProvider,
        executor: Any = None,
        events: EventBus | None = None,
        *,
        context_builder: ContextBuilder | None = None,
        max_steps: int = 8,
        thread_pool: ThreadPoolExecutor | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.provider = provider
        self.executor = executor
        self.events = events or EventBus(session_factory)
        self.context = context_builder or ContextBuilder(session_factory)
        self.max_steps = max_steps
        self.thread_pool = thread_pool

    async def _check_cancelled(self, callback: Callable[[], bool | Awaitable[bool]] | None) -> None:
        if callback is None:
            return
        result = callback()
        if inspect.isawaitable(result):
            result = await result
        if result:
            raise AgentCancelled()

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        thread_pool: ThreadPoolExecutor | None,
    ) -> Any:
        method = self.provider.complete
        call = functools.partial(method, messages, tools=tools, model=model)
        if inspect.iscoroutinefunction(method):
            return await call()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(thread_pool or self.thread_pool, call)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _stream(
        self,
        task: Task,
        context: AgentContext,
        cancel_check: Callable[[], bool | Awaitable[bool]] | None,
    ) -> dict[str, Any]:
        await self.events.emit(
            task.user_id,
            "agent.started",
            {"model": context.model},
            task_id=task.id,
            goal_id=task.goal_id,
        )
        chunks: list[str] = []
        stream = self.provider.stream_chat(context.messages, model=context.model)
        if inspect.isawaitable(stream):
            stream = await stream
        async for chunk in stream:
            await self._check_cancelled(cancel_check)
            text = str(chunk)
            chunks.append(text)
            await self.events.emit(
                task.user_id,
                "message.delta",
                {"delta": text},
                task_id=task.id,
                goal_id=task.goal_id,
                persistent=False,
            )
        content = "".join(chunks)
        await self._save_conversation_message(task, content)
        await self.events.emit(
            task.user_id,
            "agent.completed",
            {"content": content},
            task_id=task.id,
            goal_id=task.goal_id,
        )
        return {"content": content, "steps": 1, "usage": {}, "streamed": True}

    async def run(
        self,
        task: Task,
        *,
        cancel_check: Callable[[], bool | Awaitable[bool]] | None = None,
        stream: bool = False,
        thread_pool: ThreadPoolExecutor | None = None,
    ) -> dict[str, Any]:
        input_data = dict(task.input_json or {})
        checkpoint = (task.result_json or {}).get("_agent", {})
        state_messages = checkpoint.get("messages")
        if state_messages:
            messages = list(state_messages)
            context = AgentContext(messages=messages, model=input_data.get("model"))
        else:
            input_text = str(input_data.get("input", ""))
            if task.kind != "conversation" and input_data.get("title"):
                input_text = f"{input_data['title']}\n\n{input_text}"
            context = await self.context.build(
                task.user_id,
                input_text,
                conversation_id=input_data.get("conversation_id"),
                model=input_data.get("model"),
            )
            messages = context.messages

        tools = input_data.get("tools")
        if tools is None and task.kind != "conversation" and self.executor is not None:
            tools = default_agent_tools()
        if stream and not tools and not checkpoint and hasattr(self.provider, "stream_chat"):
            return await self._stream(task, context, cancel_check)

        await self.events.emit(
            task.user_id,
            "agent.started",
            {"model": context.model, "resumed": bool(checkpoint)},
            task_id=task.id,
            goal_id=task.goal_id,
        )
        usage: dict[str, int] = dict(checkpoint.get("usage", {}))
        pending = list(checkpoint.get("pending_tools", []))
        start_step = int(checkpoint.get("steps", 0))
        approved = bool(input_data.get("_approved"))
        rejected = bool(input_data.get("_approval_decided")) and not approved
        last_content = ""

        # A waiting approval resumes at the exact tool call rather than repeating earlier tools.
        if pending:
            await self._run_pending_tools(
                task,
                messages,
                pending,
                usage,
                start_step,
                approved=approved,
                rejected=rejected,
                cancel_check=cancel_check,
            )
            pending = []
            # Approval applies only to the checkpointed calls, not to future model steps.
            approved = False

        for step in range(start_step, self.max_steps):
            await self._check_cancelled(cancel_check)
            response = await self._complete(messages, tools, context.model, thread_pool)
            content = getattr(response, "content", None)
            if content is None and isinstance(response, Mapping):
                content = response.get("content")
            last_content = str(content or "")
            response_usage = _as_dict(getattr(response, "usage", None))
            if not response_usage and isinstance(response, Mapping):
                response_usage = _as_dict(response.get("usage"))
            for key, value in response_usage.items():
                if isinstance(value, int | float):
                    usage[key] = usage.get(key, 0) + int(value)

            raw_calls = getattr(response, "tool_calls", None)
            if raw_calls is None and isinstance(response, Mapping):
                raw_calls = response.get("tool_calls")
            calls = [_tool_call(call) for call in (raw_calls or [])]
            assistant: dict[str, Any] = {"role": "assistant", "content": last_content}
            if calls:
                assistant["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                        },
                    }
                    for call in calls
                ]
            messages.append(assistant)
            await self.events.emit(
                task.user_id,
                "agent.step",
                {"step": step + 1, "content": last_content, "tool_calls": calls},
                task_id=task.id,
                goal_id=task.goal_id,
            )
            if not calls:
                await self._save_conversation_message(task, last_content)
                await self.events.emit(
                    task.user_id,
                    "agent.completed",
                    {"content": last_content, "steps": step + 1},
                    task_id=task.id,
                    goal_id=task.goal_id,
                )
                return {"content": last_content, "steps": step + 1, "usage": usage}

            try:
                await self._run_pending_tools(
                    task,
                    messages,
                    calls,
                    usage,
                    step + 1,
                    approved=approved,
                    cancel_check=cancel_check,
                )
            except ApprovalRequired:
                # _run_pending_tools has already committed messages and remaining calls.
                raise

        result = {
            "content": last_content,
            "steps": self.max_steps,
            "usage": usage,
            "max_steps_reached": True,
        }
        await self._save_conversation_message(task, last_content)
        return result

    async def _run_pending_tools(
        self,
        task: Task,
        messages: list[dict[str, Any]],
        calls: list[dict[str, Any]],
        usage: dict[str, int],
        steps: int,
        *,
        approved: bool,
        rejected: bool = False,
        cancel_check: Callable[[], bool | Awaitable[bool]] | None,
    ) -> None:
        while calls:
            call = calls.pop(0)
            await self._check_cancelled(cancel_check)
            name = call["name"]
            arguments = call["arguments"]
            await self.events.emit(
                task.user_id,
                "agent.tool.started",
                {"call_id": call["id"], "name": name, "arguments": arguments},
                task_id=task.id,
                goal_id=task.goal_id,
            )
            if rejected:
                output = {
                    "status": "rejected",
                    "error": "The user rejected this tool call",
                }
            elif self.executor is None:
                output = {"error": "No executor configured"}
            else:
                kwargs: dict[str, Any] = {}
                try:
                    parameters = inspect.signature(self.executor.execute).parameters
                    supports_kwargs = any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters.values()
                    )
                    if supports_kwargs or "task_id" in parameters:
                        kwargs["task_id"] = task.id
                    if supports_kwargs or "approved" in parameters:
                        kwargs["approved"] = approved
                except (TypeError, ValueError):
                    pass
                output = await self.executor.execute(name, arguments, **kwargs)
                if not isinstance(output, dict):
                    output = {"result": output}

            needs_approval = bool(output.get("approval_required")) or output.get("status") in {
                "approval_required",
                "requires_approval",
            }
            if needs_approval and not approved:
                approval_id = await self._require_approval(
                    task,
                    name,
                    arguments,
                    output,
                    messages,
                    [call, *calls],
                    usage,
                    steps,
                )
                raise ApprovalRequired(task.id, approval_id)

            await self._record_artifacts(task, output)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": _json_text(output),
                }
            )
            await self.events.emit(
                task.user_id,
                "agent.tool.rejected" if rejected else "agent.tool.completed",
                {"call_id": call["id"], "name": name, "result": output},
                task_id=task.id,
                goal_id=task.goal_id,
            )

    async def _record_artifacts(self, task: Task, output: dict[str, Any]) -> None:
        artifacts = output.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            return
        async with self.session_factory() as session:
            for item in artifacts:
                path = item if isinstance(item, str) else _as_dict(item).get("path")
                if not isinstance(path, str) or not path or path.startswith(("/", "..")):
                    continue
                existing = await session.scalar(
                    select(Artifact).where(
                        Artifact.task_id == task.id,
                        Artifact.path == path,
                        Artifact.user_id == task.user_id,
                    )
                )
                if existing is not None:
                    continue
                data = _as_dict(output.get("data"))
                size = data.get("size", 0)
                session.add(
                    Artifact(
                        user_id=task.user_id,
                        task_id=task.id,
                        name=path.rsplit("/", 1)[-1],
                        path=path,
                        size=size if isinstance(size, int) else 0,
                        content_type="image/png"
                        if path.endswith(".png")
                        else "application/octet-stream",
                    )
                )
            await session.commit()

    async def _require_approval(
        self,
        task: Task,
        action: str,
        arguments: dict[str, Any],
        response: dict[str, Any],
        messages: list[dict[str, Any]],
        pending_tools: list[dict[str, Any]],
        usage: dict[str, int],
        steps: int,
    ) -> str:
        approval_id = ""
        async with self.session_factory() as session:
            row = await session.get(Task, task.id)
            if row is None:
                raise ValueError("Task not found")
            existing = (
                await session.scalars(
                    select(Approval).where(
                        Approval.task_id == task.id, Approval.status == "pending"
                    )
                )
            ).first()
            approval = existing or Approval(
                user_id=task.user_id,
                task_id=task.id,
                action=action,
                payload_json={"arguments": arguments, "response": response},
                expires_at=utcnow() + timedelta(hours=24),
            )
            session.add(approval)
            row.status = "waiting_approval"
            row.lease_until = None
            row.heartbeat_at = None
            row.result_json = {
                "_agent": {
                    "messages": messages,
                    "pending_tools": pending_tools,
                    "usage": usage,
                    "steps": steps,
                }
            }
            await session.commit()
            approval_id = approval.id
        await self.events.emit(
            task.user_id,
            "task.approval_required",
            {"approval_id": approval_id, "action": action, "arguments": arguments},
            task_id=task.id,
            goal_id=task.goal_id,
        )
        return approval_id

    async def _save_conversation_message(self, task: Task, content: str) -> None:
        input_data = task.input_json or {}
        conversation_id = input_data.get("conversation_id")
        if not conversation_id:
            return
        async with self.session_factory() as session:
            existing = (
                await session.scalars(
                    select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.turn_id == task.id,
                        Message.role == "assistant",
                    )
                )
            ).first()
            if existing is None:
                session.add(
                    Message(
                        conversation_id=conversation_id,
                        user_id=task.user_id,
                        role="assistant",
                        content=content,
                        turn_id=task.id,
                    )
                )
            else:
                existing.content = content
                existing.status = "completed"
            await session.commit()
