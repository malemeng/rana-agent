from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

import httpx

from .base import ChatResult

DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
CHAT_COMPLETIONS_PATH = "/chat/completions"


class MiMoError(RuntimeError):
    """Base exception for MiMo transport and protocol failures."""


class MiMoConfigurationError(MiMoError, ValueError):
    """Raised when a request is attempted without the required configuration."""


class MiMoProtocolError(MiMoError, ValueError):
    """Raised when a successful response does not follow the MiMo protocol."""


class MiMoAPIError(MiMoError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Expected a mapping-like message, got {type(value).__name__}")


def _text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _message_content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return ""
    message = choice.get("message") or choice.get("delta") or {}
    if not isinstance(message, Mapping):
        return ""
    return _text_content(message.get("content"))


async def _sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """Decode SSE events, including multi-line data fields and comment lines."""

    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line == "data":
            data_lines.append("")
        elif line.startswith("data:"):
            value = line[5:]
            data_lines.append(value[1:] if value.startswith(" ") else value)
    if data_lines:
        yield "\n".join(data_lines)


def _decode_base64_audio(value: str) -> bytes:
    data = value.strip()
    if not data:
        return b""
    if data.startswith("data:"):
        header, separator, data = data.partition(",")
        if not separator or ";base64" not in header.lower():
            raise MiMoProtocolError("Audio data URL must contain base64 data")
    data = "".join(data.split())
    data += "=" * (-len(data) % 4)
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MiMoProtocolError("Invalid base64 audio payload") from exc


def _audio_from_node(node: Any) -> list[bytes]:
    chunks: list[bytes] = []
    if isinstance(node, str):
        decoded = _decode_base64_audio(node)
        if decoded:
            chunks.append(decoded)
    elif isinstance(node, (bytes, bytearray)):
        if node:
            chunks.append(bytes(node))
    elif isinstance(node, list):
        for item in node:
            chunks.extend(_audio_from_node(item))
    elif isinstance(node, Mapping):
        for key in ("data", "b64_json", "base64", "audio"):
            if key in node:
                chunks.extend(_audio_from_node(node[key]))
    return chunks


def _audio_chunks(payload: Mapping[str, Any]) -> list[bytes]:
    chunks: list[bytes] = []
    for key in ("audio", "output_audio"):
        if key in payload:
            chunks.extend(_audio_from_node(payload[key]))
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            for branch_name in ("delta", "message"):
                branch = choice.get(branch_name)
                if isinstance(branch, Mapping):
                    for key in ("audio", "output_audio"):
                        if key in branch:
                            chunks.extend(_audio_from_node(branch[key]))
    elif "data" in payload:
        # Some gateways flatten the audio delta to {"data": "..."}.
        chunks.extend(_audio_from_node(payload["data"]))
    return chunks


class MiMoProvider:
    """Async Xiaomi MiMo chat, ASR and streaming PCM16 TTS client."""

    provider_name = "mimo"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "mimo-v2.6-flash",
        asr_model: str = "mimo-v2.5-asr",
        tts_model: str = "mimo-v2.5-tts",
        voice: str = "mimo_default",
        timeout: float = 90.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        max_backoff: float = 8.0,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.asr_model = asr_model
        self.tts_model = tts_model
        self.voice = voice
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_backoff = max_backoff
        self._sleep = sleeper or asyncio.sleep
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            transport=transport, timeout=timeout, trust_env=False
        )
        self.endpoint = (
            self.base_url
            if self.base_url.endswith(CHAT_COMPLETIONS_PATH)
            else f"{self.base_url}{CHAT_COMPLETIONS_PATH}"
        )

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> MiMoProvider:
        values = {
            "api_key": settings.mimo_api_key,
            "base_url": settings.mimo_base_url,
            "model": settings.mimo_llm_model,
            "asr_model": settings.mimo_asr_model,
            "tts_model": settings.mimo_tts_model,
            "voice": settings.mimo_voice,
            "timeout": settings.request_timeout_seconds,
            "max_retries": settings.max_retries,
        }
        values.update(overrides)
        return cls(**values)

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise MiMoConfigurationError("MiMo api_key is required")
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, min(float(retry_after), self.max_backoff))
                except ValueError:
                    pass
        return min(self.backoff_factor * (2**attempt), self.max_backoff)

    @staticmethod
    def _is_retryable(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code < 600

    @staticmethod
    def _error_detail(response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return response.text
        if isinstance(payload, Mapping) and "error" in payload:
            return payload["error"]
        return payload

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = self._error_detail(response)
        raise MiMoAPIError(
            f"MiMo request failed with HTTP {response.status_code}",
            status_code=response.status_code,
            detail=detail,
        )

    def _validate_payload(self, body: Mapping[str, Any]) -> None:
        """Hook for providers with an additional business-status envelope."""
        if body.get("error"):
            raise MiMoAPIError("Provider returned an error", detail=body["error"])

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **options: Any,
    ) -> ChatResult:
        serialized_messages = [_as_dict(message) for message in messages]
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": serialized_messages,
            **options,
            "stream": False,
        }
        if tools is not None:
            payload["tools"] = [_as_dict(tool) for tool in tools]

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(
                    self.endpoint,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise MiMoAPIError("MiMo transport failed", detail=str(exc)) from exc
                await self._sleep(self._retry_delay(attempt))
                continue

            if self._is_retryable(response.status_code) and attempt < self.max_retries:
                delay = self._retry_delay(attempt, response)
                await response.aread()
                await self._sleep(delay)
                continue
            self._raise_for_status(response)
            try:
                body = response.json()
            except json.JSONDecodeError as exc:
                raise MiMoProtocolError("MiMo returned a non-JSON completion") from exc
            if not isinstance(body, Mapping):
                raise MiMoProtocolError("MiMo completion must be a JSON object")
            self._validate_payload(body)

            choices = body.get("choices") or []
            message: Mapping[str, Any] = {}
            if choices and isinstance(choices[0], Mapping):
                candidate = choices[0].get("message") or {}
                if isinstance(candidate, Mapping):
                    message = candidate
            raw_tool_calls = message.get("tool_calls") or []
            tool_calls = [dict(call) for call in raw_tool_calls if isinstance(call, Mapping)]
            usage = body.get("usage") or {}
            return ChatResult(
                content=_text_content(message.get("content")),
                tool_calls=tool_calls,
                usage=dict(usage) if isinstance(usage, Mapping) else {},
            )
        raise AssertionError("retry loop always returns or raises")

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **options: Any,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [_as_dict(message) for message in messages],
            **options,
            "stream": True,
        }
        for attempt in range(self.max_retries + 1):
            retry_delay: float | None = None
            try:
                async with self._client.stream(
                    "POST",
                    self.endpoint,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                ) as response:
                    if self._is_retryable(response.status_code) and attempt < self.max_retries:
                        retry_delay = self._retry_delay(attempt, response)
                        await response.aread()
                    else:
                        if response.status_code >= 400:
                            await response.aread()
                        self._raise_for_status(response)
                        async for data in _sse_data(response.aiter_lines()):
                            if data.strip() == "[DONE]":
                                return
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError as exc:
                                raise MiMoProtocolError("Invalid JSON in MiMo SSE event") from exc
                            if not isinstance(event, Mapping):
                                continue
                            self._validate_payload(event)
                            content = _message_content(event)
                            if content:
                                yield content
                        return
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise MiMoAPIError("MiMo stream transport failed", detail=str(exc)) from exc
                retry_delay = self._retry_delay(attempt)
            if retry_delay is not None:
                await self._sleep(retry_delay)
        raise AssertionError("retry loop always returns or raises")

    async def transcribe(
        self,
        audio_base64: str,
        audio_format: str = "wav",
        language: str | None = "auto",
        model: str | None = None,
    ) -> str:
        audio_format = audio_format.lower()
        if audio_format not in {"wav", "mp3"}:
            raise ValueError("audio_format must be wav or mp3")
        input_audio: dict[str, str] = {
            "data": audio_base64,
            "format": audio_format,
        }
        options: dict[str, Any] = {}
        if language:
            if language not in {"auto", "zh", "en"}:
                raise ValueError("language must be auto, zh or en")
            options["asr_options"] = {"language": language}
        result = await self.complete(
            [
                {
                    "role": "user",
                    "content": [{"type": "input_audio", "input_audio": input_audio}],
                }
            ],
            model=model or self.asr_model,
            **options,
        )
        return result.content

    # Friendly aliases for callers that use ASR terminology.
    transcribe_audio = transcribe
    asr = transcribe

    async def stream_tts(
        self,
        text: str,
        voice: str | None = None,
        model: str | None = None,
        instructions: str | None = None,
    ) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        messages: list[dict[str, Any]] = []
        if instructions:
            messages.append({"role": "user", "content": instructions})
        messages.append({"role": "assistant", "content": text})
        payload = {
            "model": model or self.tts_model,
            "messages": messages,
            "audio": {"voice": voice or self.voice, "format": "pcm16"},
            "stream": True,
        }

        for attempt in range(self.max_retries + 1):
            retry_delay: float | None = None
            try:
                async with self._client.stream(
                    "POST",
                    self.endpoint,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                ) as response:
                    if self._is_retryable(response.status_code) and attempt < self.max_retries:
                        retry_delay = self._retry_delay(attempt, response)
                        await response.aread()
                    else:
                        if response.status_code >= 400:
                            await response.aread()
                        self._raise_for_status(response)
                        async for data in _sse_data(response.aiter_lines()):
                            if data.strip() == "[DONE]":
                                return
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                # Tolerate gateways that put bare base64 in data fields.
                                chunk = _decode_base64_audio(data)
                                if chunk:
                                    yield chunk
                                continue
                            if not isinstance(event, Mapping):
                                continue
                            self._validate_payload(event)
                            for chunk in _audio_chunks(event):
                                yield chunk
                        return
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise MiMoAPIError("MiMo TTS transport failed", detail=str(exc)) from exc
                retry_delay = self._retry_delay(attempt)
            if retry_delay is not None:
                await self._sleep(retry_delay)
        raise AssertionError("retry loop always returns or raises")

    synthesize = stream_tts
    synthesize_stream = stream_tts
    tts = stream_tts

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    close = aclose

    async def __aenter__(self) -> MiMoProvider:
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        await self.aclose()
