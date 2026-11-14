import codecs
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from app.errors import ToolError
from app.models import TASK_ID_PATTERN

_TASK_ID_RE = re.compile(TASK_ID_PATTERN)


class WorkspaceStore:
    def __init__(
        self,
        root: Path,
        max_file_bytes: int = 1024 * 1024,
        max_list_entries: int = 1000,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.max_file_bytes = max_file_bytes
        self.max_list_entries = max_list_entries
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._chmod(self.root, 0o700)

    @staticmethod
    def _chmod(path: Path, mode: int) -> None:
        try:
            path.chmod(mode)
        except OSError:
            # Windows exposes only a subset of POSIX permission bits.
            pass

    def task_root(self, task_id: str, create: bool = False) -> Path:
        if not isinstance(task_id, str) or _TASK_ID_RE.fullmatch(task_id) is None:
            raise ToolError("INVALID_TASK_ID", "task_id 格式无效")
        root = self.root / task_id
        if root.is_symlink():
            raise ToolError("PATH_TRAVERSAL", "任务目录不得为符号链接")
        if create:
            root.mkdir(parents=False, exist_ok=True, mode=0o700)
            self._chmod(root, 0o700)
        return root

    @staticmethod
    def _relative_path(raw_path: Any) -> PurePosixPath:
        if not isinstance(raw_path, str) or "\x00" in raw_path:
            raise ToolError("INVALID_PATH", "path 必须是有效字符串")
        if "\\" in raw_path:
            raise ToolError("INVALID_PATH", "path 必须使用正斜杠且不得包含反斜杠")
        if PureWindowsPath(raw_path).drive:
            raise ToolError("PATH_TRAVERSAL", "不允许绝对路径或驱动器路径")
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise ToolError("PATH_TRAVERSAL", "path 不得越出任务目录")
        return relative

    @staticmethod
    def _assert_contained(candidate: Path, task_root: Path) -> None:
        try:
            candidate.relative_to(task_root)
        except ValueError as exc:
            raise ToolError("PATH_TRAVERSAL", "path 不得越出任务目录") from exc

    def resolve(
        self,
        task_id: str,
        raw_path: Any = ".",
        *,
        create_task: bool = False,
        allow_root: bool = False,
    ) -> tuple[Path, Path, str]:
        """Return (resolved target, resolved task root, normalized relative path)."""

        root = self.task_root(task_id, create=create_task).resolve()
        relative = self._relative_path(raw_path)
        normalized = relative.as_posix()
        if not allow_root and normalized == ".":
            raise ToolError("INVALID_PATH", "文件操作需要任务目录内的相对路径")

        candidate = root.joinpath(*relative.parts)
        if candidate.is_symlink():
            raise ToolError("SYMLINK_NOT_ALLOWED", "文件操作不得直接跟随符号链接")
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ToolError("INVALID_PATH", "path 无法安全解析") from exc
        self._assert_contained(resolved, root)
        return resolved, root, normalized

    def _prepare_parent(self, target: Path, root: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved_parent = target.parent.resolve()
        self._assert_contained(resolved_parent, root)
        current = resolved_parent
        while current != root:
            self._chmod(current, 0o700)
            current = current.parent

    def atomic_write(self, target: Path, root: Path, content: bytes) -> None:
        if len(content) > self.max_file_bytes:
            raise ToolError(
                "FILE_TOO_LARGE",
                "文件超过大小限制",
                {"max_file_bytes": self.max_file_bytes, "actual_bytes": len(content)},
            )
        self._prepare_parent(target, root)
        fd, temporary_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            self._chmod(temporary, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            self._chmod(target, 0o600)
            if os.name == "posix":
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError as exc:
            raise ToolError("WORKSPACE_IO_ERROR", "原子写入失败") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def write_bytes(self, task_id: str, raw_path: Any, content: bytes) -> dict[str, Any]:
        if not isinstance(content, bytes):
            raise ToolError("INVALID_ARGUMENTS", "二进制内容必须是 bytes")
        target, root, normalized = self.resolve(task_id, raw_path, create_task=True)
        self.atomic_write(target, root, content)
        return {"path": normalized, "size": len(content)}

    def write_text(self, task_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolError("INVALID_ARGUMENTS", "workspace_write.content 必须是字符串")
        encoding = self._encoding(arguments.get("encoding", "utf-8"))
        try:
            encoded = content.encode(encoding)
        except UnicodeError as exc:
            raise ToolError("ENCODING_ERROR", "内容无法按指定编码写入") from exc
        result = self.write_bytes(task_id, arguments.get("path"), encoded)
        result["encoding"] = encoding
        return result

    def read_text(self, task_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        target, _, normalized = self.resolve(task_id, arguments.get("path"))
        if not target.exists() or not target.is_file():
            raise ToolError("FILE_NOT_FOUND", "文件不存在", {"path": normalized})
        encoding = self._encoding(arguments.get("encoding", "utf-8"))
        try:
            with target.open("rb") as handle:
                content = handle.read(self.max_file_bytes + 1)
        except OSError as exc:
            raise ToolError("WORKSPACE_IO_ERROR", "文件读取失败") from exc
        if len(content) > self.max_file_bytes:
            raise ToolError(
                "FILE_TOO_LARGE",
                "文件超过读取大小限制",
                {"max_file_bytes": self.max_file_bytes},
            )
        try:
            text = content.decode(encoding)
        except UnicodeError as exc:
            raise ToolError(
                "ENCODING_ERROR", "文件不是指定编码的文本", {"encoding": encoding}
            ) from exc
        return {
            "path": normalized,
            "content": text,
            "size": len(content),
            "encoding": encoding,
        }

    @staticmethod
    def _encoding(value: Any) -> str:
        if not isinstance(value, str):
            raise ToolError("INVALID_ARGUMENTS", "encoding 必须是字符串")
        try:
            return codecs.lookup(value).name
        except LookupError as exc:
            raise ToolError("ENCODING_ERROR", "未知文本编码") from exc

    def list_entries(self, task_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        target, root, normalized = self.resolve(
            task_id, arguments.get("path", "."), create_task=True, allow_root=True
        )
        if not target.exists():
            raise ToolError("DIRECTORY_NOT_FOUND", "目录不存在", {"path": normalized})
        if not target.is_dir():
            raise ToolError("NOT_A_DIRECTORY", "path 不是目录", {"path": normalized})
        recursive = arguments.get("recursive", False)
        if not isinstance(recursive, bool):
            raise ToolError("INVALID_ARGUMENTS", "recursive 必须是布尔值")
        limit = arguments.get("limit", self.max_list_entries)
        if type(limit) is not int or limit <= 0:
            raise ToolError("INVALID_ARGUMENTS", "limit 必须是正整数")
        limit = min(limit, self.max_list_entries)

        def walk(directory: Path):
            try:
                children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
            except OSError as exc:
                raise ToolError("WORKSPACE_IO_ERROR", "目录读取失败") from exc
            for child in children:
                yield child
                if recursive and not child.is_symlink() and child.is_dir():
                    yield from walk(child)

        entries: list[dict[str, Any]] = []
        truncated = False
        for entry in walk(target):
            if len(entries) >= limit:
                truncated = True
                break
            try:
                metadata = entry.lstat()
                relative = entry.relative_to(root).as_posix()
                if entry.is_symlink():
                    entry_type = "symlink"
                elif stat.S_ISDIR(metadata.st_mode):
                    entry_type = "directory"
                elif stat.S_ISREG(metadata.st_mode):
                    entry_type = "file"
                else:
                    entry_type = "other"
                item: dict[str, Any] = {"path": relative, "type": entry_type}
                if entry_type == "file":
                    item["size"] = metadata.st_size
                entries.append(item)
            except OSError as exc:
                raise ToolError("WORKSPACE_IO_ERROR", "目录项读取失败") from exc
        return {
            "path": normalized,
            "entries": entries,
            "truncated": truncated,
            "limit": limit,
        }

    def delete(self, task_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        target, _, normalized = self.resolve(task_id, arguments.get("path"))
        recursive = arguments.get("recursive", False)
        if not isinstance(recursive, bool):
            raise ToolError("INVALID_ARGUMENTS", "recursive 必须是布尔值")
        if not target.exists() and not target.is_symlink():
            raise ToolError("FILE_NOT_FOUND", "目标不存在", {"path": normalized})
        try:
            if target.is_dir() and not target.is_symlink():
                if recursive:
                    shutil.rmtree(target)
                else:
                    target.rmdir()
                deleted_type = "directory"
            else:
                target.unlink()
                deleted_type = "file"
        except OSError as exc:
            raise ToolError("WORKSPACE_IO_ERROR", "删除失败；非空目录需 recursive=true") from exc
        return {"path": normalized, "deleted": True, "type": deleted_type}
