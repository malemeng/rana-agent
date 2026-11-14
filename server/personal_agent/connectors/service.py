from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Connection, Task
from .base import Connector, ConnectorError
from .caldav import CalDAVConnector
from .imap import IMAPConnector
from .rss import RSSConnector

CONNECTOR_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "caldav_read",
            "description": "读取用户已连接 CalDAV 日历中的日程，不修改远端数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "start": {"type": "string", "description": "如 20261115T000000Z"},
                    "end": {"type": "string", "description": "如 20261116T000000Z"},
                },
                "required": ["connection_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "imap_read",
            "description": "以只读方式检索已连接邮箱的邮件摘要和正文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "unseen_only": {"type": "boolean"},
                },
                "required": ["connection_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rss_read",
            "description": "读取已连接 RSS/Atom 信息源的最新条目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["connection_id"],
            },
        },
    },
]


class ConnectorToolExecutor:
    """Add owner-scoped read-only connectors in front of the sandbox executor."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        executor: Any,
    ) -> None:
        self.session_factory = session_factory
        self.executor = executor

    async def _connector(self, task_id: str, connection_id: str) -> Connector:
        async with self.session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise ConnectorError("任务不存在")
            connection = (
                await session.execute(
                    select(Connection).where(
                        Connection.id == connection_id,
                        Connection.user_id == task.user_id,
                        Connection.enabled.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if connection is None:
                raise ConnectorError("连接器不存在或已停用")
            config = dict(connection.config_json)
            if connection.kind == "caldav":
                return CalDAVConnector(**config)
            if connection.kind == "imap":
                return IMAPConnector(**config)
            if connection.kind == "rss":
                return RSSConnector(**config)
            raise ConnectorError(f"不支持的连接器类型：{connection.kind}")

    async def execute(
        self,
        action: str,
        arguments: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        timeout_seconds: float | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        arguments = dict(arguments or {})
        if action not in {"caldav_read", "imap_read", "rss_read"}:
            return await self.executor.execute(
                action,
                arguments,
                task_id=task_id,
                timeout_seconds=timeout_seconds,
                approved=approved,
            )
        if not task_id:
            return {"ok": False, "error": {"code": "TASK_REQUIRED", "message": "缺少 task_id"}}
        try:
            connector = await self._connector(task_id, str(arguments.pop("connection_id", "")))
            data = await connector.read(**arguments)
            return {"ok": True, "data": data, "artifacts": []}
        except (ConnectorError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "error": {"code": "CONNECTOR_ERROR", "message": str(exc)},
                "artifacts": [],
            }
