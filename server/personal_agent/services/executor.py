from __future__ import annotations

import asyncio
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


class ExecutorError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None, response: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ExecutorClient:
    """Small asynchronous client for the executor's tool endpoint."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout_seconds: float = 120.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
                headers=headers,
            )
        return self._client

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(60.0, max(0.0, float(retry_after)))
                except ValueError:
                    try:
                        from datetime import UTC, datetime

                        target = parsedate_to_datetime(retry_after)
                        if target.tzinfo is None:
                            target = target.replace(tzinfo=UTC)
                        return min(
                            60.0,
                            max(0.0, (target - datetime.now(UTC)).total_seconds()),
                        )
                    except (TypeError, ValueError):
                        pass
        return min(8.0, 0.2 * (2**attempt))

    async def execute(
        self,
        action: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        task_id: str | None = None,
        timeout_seconds: float | None = None,
        approved: bool = False,
    ) -> dict[str, Any]:
        effective_timeout = timeout_seconds or self.timeout_seconds
        body: dict[str, Any] = {
            "tool": action,
            "arguments": dict(arguments or {}),
            "task_id": task_id or "ad-hoc",
            "timeout_seconds": effective_timeout,
        }
        if approved:
            body["approved"] = True
        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = await self.client.post(
                    "/api/v1/execute",
                    json=body,
                    headers=({"Authorization": f"Bearer {self.token}"} if self.token else None),
                    timeout=effective_timeout,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._retry_delay(response, attempt))
                        continue
                response.raise_for_status()
                if not response.content:
                    return {}
                data = response.json()
                return data if isinstance(data, dict) else {"result": data}
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(self._retry_delay(None, attempt))
                    continue
            except httpx.HTTPStatusError as exc:
                try:
                    error_body: Any = response.json() if response is not None else None
                except ValueError:
                    error_body = response.text if response is not None else None
                detail = (
                    error_body.get("detail")
                    if isinstance(error_body, dict) and "detail" in error_body
                    else error_body
                )
                raise ExecutorError(
                    f"Executor request failed ({response.status_code if response else 'unknown'}): "
                    f"{detail or exc}",
                    status_code=response.status_code if response else None,
                    response=error_body,
                ) from exc
            except ValueError as exc:
                raise ExecutorError("Executor returned invalid JSON") from exc
        raise ExecutorError(f"Executor request failed after retries: {last_error}") from last_error

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def aclose(self) -> None:
        await self.close()

    async def __aenter__(self) -> ExecutorClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
