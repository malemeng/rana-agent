import asyncio
import os
import shlex
import signal
import subprocess
from typing import Any

from app.errors import ToolError
from app.models import ErrorBody, ToolOutcome
from app.workspace import WorkspaceStore


class ShellTool:
    def __init__(self, workspace: WorkspaceStore, output_limit: int) -> None:
        self.workspace = workspace
        self.output_limit = output_limit

    @staticmethod
    def _parse_command(arguments: dict[str, Any]) -> list[str]:
        argv = arguments.get("argv")
        command = arguments.get("command")
        if (argv is None) == (command is None):
            raise ToolError("INVALID_ARGUMENTS", "shell 必须且只能提供 argv 或 command 之一")
        if argv is not None:
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(item, str) or not item for item in argv)
            ):
                raise ToolError("INVALID_ARGUMENTS", "argv 必须是非空字符串数组")
            parsed = argv
        else:
            if not isinstance(command, str) or not command.strip():
                raise ToolError("INVALID_ARGUMENTS", "command 必须是非空字符串")
            try:
                parsed = shlex.split(command, posix=os.name != "nt")
            except ValueError as exc:
                raise ToolError("INVALID_ARGUMENTS", "command 引号或转义无效") from exc
            if os.name == "nt":
                parsed = [ShellTool._strip_outer_quotes(item) for item in parsed]
        if not parsed or any("\x00" in item for item in parsed):
            raise ToolError("INVALID_ARGUMENTS", "命令参数不得为空或包含 NUL")
        return parsed

    @staticmethod
    def _strip_outer_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value

    async def _read_stream(self, stream: asyncio.StreamReader | None) -> tuple[str, int, bool]:
        if stream is None:
            return "", 0, False
        retained = bytearray()
        total = 0
        truncated = False
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            remaining = self.output_limit - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > max(remaining, 0):
                truncated = True
        return retained.decode("utf-8", errors="replace"), total, truncated

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        await process.wait()

    async def execute(self, task_id: str, arguments: dict[str, Any], timeout: float) -> ToolOutcome:
        argv = self._parse_command(arguments)
        cwd, _, cwd_path = self.workspace.resolve(
            task_id,
            arguments.get("cwd", "."),
            create_task=True,
            allow_root=True,
        )
        if not cwd.exists() or not cwd.is_dir():
            raise ToolError("DIRECTORY_NOT_FOUND", "shell.cwd 不是已存在目录", {"path": cwd_path})

        process_options: dict[str, Any] = {}
        if os.name == "posix":
            process_options["start_new_session"] = True
        elif os.name == "nt":
            process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                **process_options,
            )
        except FileNotFoundError as exc:
            raise ToolError(
                "EXECUTABLE_NOT_FOUND", "shell 可执行文件不存在", {"executable": argv[0]}
            ) from exc
        except (OSError, ValueError) as exc:
            raise ToolError("PROCESS_START_FAILED", "shell 进程启动失败") from exc

        stdout_task = asyncio.create_task(self._read_stream(process.stdout))
        stderr_task = asyncio.create_task(self._read_stream(process.stderr))
        timed_out = False
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            await self._kill_process(process)
            exit_code = None
        except asyncio.CancelledError:
            await self._kill_process(process)
            await asyncio.gather(stdout_task, stderr_task)
            raise

        stdout, stdout_bytes, stdout_truncated = await stdout_task
        stderr, stderr_bytes, stderr_truncated = await stderr_task
        data = {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "output_limit_bytes": self.output_limit,
        }
        if timed_out:
            data["timeout_seconds"] = timeout
            return ToolOutcome(
                ok=False,
                data=data,
                error=ErrorBody(code="SHELL_TIMEOUT", message="shell 执行超时"),
            )
        if exit_code != 0:
            return ToolOutcome(
                ok=False,
                data=data,
                error=ErrorBody(
                    code="NON_ZERO_EXIT",
                    message="shell 进程返回非零退出码",
                    details={"exit_code": exit_code},
                ),
            )
        return ToolOutcome(data=data)
