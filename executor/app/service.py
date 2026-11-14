import asyncio
import logging
import time

from app.browser import BrowserTool
from app.config import Settings
from app.errors import ToolError
from app.models import ErrorBody, ExecuteRequest, ToolOutcome, ToolResult
from app.shell import ShellTool
from app.workspace import WorkspaceStore

logger = logging.getLogger("executor.service")


class ExecutorService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.workspace = WorkspaceStore(
            settings.workspace_root,
            max_file_bytes=settings.max_file_bytes,
            max_list_entries=settings.max_list_entries,
        )
        self.shell = ShellTool(self.workspace, settings.shell_output_bytes)
        self.browser = BrowserTool(
            self.workspace,
            headless=settings.browser_headless,
            max_text_chars=settings.browser_max_text_chars,
        )

    def _workspace_tool(self, request: ExecuteRequest) -> ToolOutcome:
        task_id = request.task_id
        arguments = request.arguments
        if request.tool == "workspace_list":
            return ToolOutcome(data=self.workspace.list_entries(task_id, arguments))
        if request.tool == "workspace_read":
            return ToolOutcome(data=self.workspace.read_text(task_id, arguments))
        if request.tool == "workspace_write":
            data = self.workspace.write_text(task_id, arguments)
            return ToolOutcome(data=data, artifacts=[data["path"]])
        if request.tool == "workspace_delete":
            return ToolOutcome(data=self.workspace.delete(task_id, arguments))
        raise ToolError("UNKNOWN_TOOL", "未知工具", {"tool": request.tool})

    async def execute(self, request: ExecuteRequest) -> ToolResult:
        started = time.perf_counter()
        timeout = request.timeout_seconds or self.settings.default_timeout_seconds
        timeout = min(timeout, self.settings.max_timeout_seconds)
        if (
            request.tool == "browser"
            and request.arguments.get("action") in {"click", "fill"}
            and not request.approved
        ):
            return ToolResult(
                task_id=request.task_id,
                tool=request.tool,
                ok=True,
                data={"arguments": request.arguments},
                approval_required=True,
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )
        try:
            if request.tool.startswith("workspace_"):
                outcome = self._workspace_tool(request)
            elif request.tool == "shell":
                outcome = await self.shell.execute(request.task_id, request.arguments, timeout)
            elif request.tool == "browser":
                outcome = await asyncio.wait_for(
                    self.browser.execute(request.task_id, request.arguments, timeout),
                    timeout=timeout,
                )
            else:
                raise ToolError("UNKNOWN_TOOL", "未知工具", {"tool": request.tool})
        except ToolError as exc:
            outcome = ToolOutcome(ok=False, error=exc.as_body())
        except TimeoutError:
            outcome = ToolOutcome(
                ok=False,
                error=ErrorBody(
                    code="EXECUTION_TIMEOUT",
                    message="工具执行超时",
                    details={"timeout_seconds": timeout},
                ),
            )
        except Exception:
            # Never log request arguments, process output, tokens, or file contents.
            logger.exception(
                "tool execution failed task_id=%s tool=%s",
                request.task_id,
                request.tool,
            )
            outcome = ToolOutcome(
                ok=False,
                error=ErrorBody(code="INTERNAL_ERROR", message="工具内部执行错误"),
            )

        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        result = ToolResult(
            task_id=request.task_id,
            tool=request.tool,
            ok=outcome.ok,
            data=outcome.data,
            error=outcome.error,
            artifacts=outcome.artifacts,
            duration_ms=duration_ms,
        )
        logger.info(
            "tool completed task_id=%s tool=%s ok=%s duration_ms=%d",
            request.task_id,
            request.tool,
            result.ok,
            result.duration_ms,
        )
        return result

    async def close(self) -> None:
        await self.browser.close()
