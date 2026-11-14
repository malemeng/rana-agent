import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.errors import ToolError
from app.models import ToolOutcome
from app.workspace import WorkspaceStore


class BrowserTool:
    """Optional Playwright-backed browser with one isolated context per task."""

    def __init__(
        self,
        workspace: WorkspaceStore,
        headless: bool = True,
        max_text_chars: int = 200_000,
    ) -> None:
        self.workspace = workspace
        self.headless = headless
        self.max_text_chars = max_text_chars
        self._playwright: Any = None
        self._browser: Any = None
        self._sessions: dict[str, tuple[Any, Any]] = {}
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._lifecycle_lock = asyncio.Lock()

    async def _ensure_browser(self) -> Any:
        async with self._lifecycle_lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise ToolError(
                    "BROWSER_UNAVAILABLE",
                    "Playwright 未安装",
                    {
                        "reason": "playwright_package_missing",
                        "install": "pip install -e '.[browser]' && playwright install chromium",
                    },
                ) from exc
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=self.headless)
                return self._browser
            except Exception as exc:
                if self._playwright is not None:
                    await self._playwright.stop()
                self._playwright = None
                self._browser = None
                raise ToolError(
                    "BROWSER_UNAVAILABLE",
                    "Chromium 浏览器未安装或启动失败",
                    {
                        "reason": type(exc).__name__,
                        "install": "playwright install chromium",
                    },
                ) from exc

    async def _get_page(self, task_id: str, create: bool) -> Any:
        session = self._sessions.get(task_id)
        if session is not None:
            return session[1]
        if not create:
            raise ToolError(
                "BROWSER_SESSION_NOT_FOUND", "请先执行 browser open，或在参数中提供 url"
            )
        browser = await self._ensure_browser()
        async with self._lifecycle_lock:
            session = self._sessions.get(task_id)
            if session is None:
                context = await browser.new_context()
                page = await context.new_page()
                self._sessions[task_id] = (context, page)
                return page
            return session[1]

    def _task_lock(self, task_id: str) -> asyncio.Lock:
        return self._task_locks.setdefault(task_id, asyncio.Lock())

    @staticmethod
    def _timeout_ms(timeout: float) -> int:
        return max(1, int(timeout * 1000))

    @staticmethod
    def _wait_until(arguments: dict[str, Any]) -> str:
        value = arguments.get("wait_until", "load")
        if value not in {"load", "domcontentloaded", "networkidle"}:
            raise ToolError(
                "INVALID_ARGUMENTS",
                "wait_until 仅支持 load、domcontentloaded、networkidle",
            )
        return value

    async def _navigate_if_requested(
        self, page: Any, arguments: dict[str, Any], timeout_ms: int
    ) -> None:
        url = arguments.get("url")
        if url is not None:
            if not isinstance(url, str) or not url:
                raise ToolError("INVALID_ARGUMENTS", "url 必须是非空字符串")
            await page.goto(
                url,
                wait_until=self._wait_until(arguments),
                timeout=timeout_ms,
            )

    @asynccontextmanager
    async def _action_errors(self) -> AsyncIterator[None]:
        try:
            yield
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                "BROWSER_ACTION_FAILED",
                "浏览器操作失败",
                {"reason": type(exc).__name__},
            ) from exc

    async def execute(self, task_id: str, arguments: dict[str, Any], timeout: float) -> ToolOutcome:
        action = arguments.get("action")
        if action not in {"open", "read", "click", "fill", "screenshot"}:
            raise ToolError(
                "INVALID_ARGUMENTS",
                "browser.action 仅支持 open、read、click、fill、screenshot",
            )
        timeout_ms = self._timeout_ms(timeout)
        async with self._task_lock(task_id):
            async with self._action_errors():
                if action == "open":
                    url = arguments.get("url")
                    if not isinstance(url, str) or not url:
                        raise ToolError("INVALID_ARGUMENTS", "browser open 需要 url")
                    page = await self._get_page(task_id, create=True)
                    response = await page.goto(
                        url,
                        wait_until=self._wait_until(arguments),
                        timeout=timeout_ms,
                    )
                    return ToolOutcome(
                        data={
                            "url": page.url,
                            "title": await page.title(),
                            "status": response.status if response is not None else None,
                        }
                    )

                page = await self._get_page(task_id, create=arguments.get("url") is not None)
                await self._navigate_if_requested(page, arguments, timeout_ms)

                if action == "read":
                    selector = arguments.get("selector")
                    if selector is not None and not isinstance(selector, str):
                        raise ToolError("INVALID_ARGUMENTS", "selector 必须是字符串")
                    locator = page.locator(selector or "body")
                    text = await locator.inner_text(timeout=timeout_ms)
                    truncated = len(text) > self.max_text_chars
                    return ToolOutcome(
                        data={
                            "url": page.url,
                            "title": await page.title(),
                            "text": text[: self.max_text_chars],
                            "text_truncated": truncated,
                            "text_chars": len(text),
                        }
                    )

                if action == "click":
                    selector = arguments.get("selector")
                    if not isinstance(selector, str) or not selector:
                        raise ToolError("INVALID_ARGUMENTS", "browser click 需要 selector")
                    await page.click(selector, timeout=timeout_ms)
                    return ToolOutcome(data={"url": page.url, "clicked": selector})

                if action == "fill":
                    selector = arguments.get("selector")
                    value = arguments.get("value", arguments.get("text"))
                    if not isinstance(selector, str) or not selector:
                        raise ToolError("INVALID_ARGUMENTS", "browser fill 需要 selector")
                    if not isinstance(value, str):
                        raise ToolError("INVALID_ARGUMENTS", "browser fill 需要字符串 value")
                    await page.fill(selector, value, timeout=timeout_ms)
                    return ToolOutcome(data={"url": page.url, "filled": selector})

                screenshot_path = arguments.get("path", "screenshot.png")
                full_page = arguments.get("full_page", False)
                if not isinstance(full_page, bool):
                    raise ToolError("INVALID_ARGUMENTS", "full_page 必须是布尔值")
                content = await page.screenshot(full_page=full_page, timeout=timeout_ms)
                written = self.workspace.write_bytes(task_id, screenshot_path, content)
                return ToolOutcome(
                    data={"url": page.url, **written},
                    artifacts=[written["path"]],
                )

        raise ToolError("BROWSER_ACTION_FAILED", "浏览器操作未产生结果")

    async def close(self) -> None:
        async with self._lifecycle_lock:
            for context, _ in self._sessions.values():
                try:
                    await context.close()
                except Exception:
                    pass
            self._sessions.clear()
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
