from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from .base import ChatResult
from .mimo import (
    MiMoAPIError,
    MiMoProtocolError,
    MiMoProvider,
    _decode_base64_audio,
    _sse_data,
)


class MiniMaxProvider(MiMoProvider):
    """MiniMax OpenAI chat plus multipart ASR and streaming PCM TTS."""

    provider_name = "minimax"
    _EMOTIONS = {
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "calm",
        "fluent",
        "whisper",
    }

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.minimax.io/v1",
        model: str = "MiniMax-M3.1-Flash",
        asr_model: str = "asr-1.0",
        tts_model: str = "speech-2.8-turbo",
        voice: str = "Chinese (Mandarin)_Lyrical_Voice",
        reasoning_effort: str = "medium",
        timeout: float = 90.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        max_backoff: float = 8.0,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Any = None,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url,
            model=model,
            asr_model=asr_model,
            tts_model=tts_model,
            voice=voice,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            max_backoff=max_backoff,
            client=client,
            transport=transport,
            sleeper=sleeper,
        )
        self.reasoning_effort = reasoning_effort
        root = self.base_url.rstrip("/")
        self.asr_endpoint = f"{root}/speech_to_text"
        self.tts_endpoint = f"{root}/t2a_v2"

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> MiniMaxProvider:
        base_url = settings.minimax_base_url
        if settings.minimax_region == "cn" and base_url == "https://api.minimax.io/v1":
            base_url = "https://api.minimax.cn/v1"
        values: dict[str, Any] = {
            "api_key": settings.minimax_api_key,
            "base_url": base_url,
            "model": settings.minimax_llm_model,
            "asr_model": settings.minimax_asr_model,
            "tts_model": settings.minimax_tts_model,
            "voice": settings.minimax_voice,
            "reasoning_effort": settings.minimax_reasoning_effort,
            "timeout": settings.request_timeout_seconds,
            "max_retries": settings.max_retries,
        }
        values.update(overrides)
        return cls(**values)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise MiMoAPIError("MiniMax api_key is required")
        return {"Authorization": f"Bearer {self.api_key}"}

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise MiMoAPIError(
                f"MiniMax request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                detail=self._error_detail(response),
            )

    def _validate_payload(self, body: Mapping[str, Any]) -> None:
        super()._validate_payload(body)
        base_response = body.get("base_resp")
        if not isinstance(base_response, Mapping):
            return
        code = base_response.get("status_code", 0)
        if code in {None, 0}:
            return
        status_code = 429 if code in {1002, 1039} else None
        raise MiMoAPIError(
            f"MiniMax business error {code}: {base_response.get('status_msg', '')}",
            status_code=status_code,
            detail=dict(base_response),
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **options: Any,
    ) -> ChatResult:
        options.setdefault("reasoning_effort", self.reasoning_effort)
        return await super().complete(messages, tools=tools, model=model, **options)

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **options: Any,
    ) -> AsyncIterator[str]:
        options.setdefault("reasoning_effort", self.reasoning_effort)
        return super().stream_chat(messages, model=model, **options)

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
        audio = _decode_base64_audio(audio_base64)
        if not audio:
            raise ValueError("audio must not be empty")
        if len(audio) > 50 * 1024 * 1024:
            raise ValueError("MiniMax ASR audio exceeds 50 MB")
        headers = self._headers()
        if language and language != "auto":
            headers["language"] = language
        mime = "audio/wav" if audio_format == "wav" else "audio/mpeg"
        payload = {"model": model or self.asr_model, "response_format": "json"}

        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.post(
                    self.asr_endpoint,
                    data=payload,
                    files={"file": (f"input.{audio_format}", audio, mime)},
                    headers=headers,
                    timeout=self.timeout,
                )
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise MiMoAPIError("MiniMax ASR transport failed", detail=str(exc)) from exc
                await self._sleep(self._retry_delay(attempt))
                continue
            if self._is_retryable(response.status_code) and attempt < self.max_retries:
                delay = self._retry_delay(attempt, response)
                await response.aread()
                await self._sleep(delay)
                continue
            self._raise_for_status(response)
            body = response.json()
            if not isinstance(body, Mapping):
                raise MiMoProtocolError("MiniMax ASR response must be a JSON object")
            self._validate_payload(body)
            text = body.get("text")
            if not isinstance(text, str):
                raise MiMoProtocolError("MiniMax ASR response is missing text")
            return text
        raise AssertionError("retry loop always returns or raises")

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
        if len(text) >= 10000:
            raise ValueError("MiniMax TTS text must be less than 10000 characters")
        voice_setting: dict[str, Any] = {
            "voice_id": voice or self.voice,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        }
        if instructions and instructions.lower() in self._EMOTIONS:
            voice_setting["emotion"] = instructions.lower()
        payload = {
            "model": model or self.tts_model,
            "text": text,
            "stream": True,
            "stream_options": {"exclude_aggregated_audio": True},
            "language_boost": "auto",
            "output_format": "hex",
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": 24000,
                "format": "pcm",
                "channel": 1,
            },
        }

        for attempt in range(self.max_retries + 1):
            retry_delay: float | None = None
            seen_chunk = False
            try:
                async with self.client.stream(
                    "POST",
                    self.tts_endpoint,
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
                                raise MiMoProtocolError(
                                    "Invalid JSON in MiniMax TTS SSE event"
                                ) from exc
                            if not isinstance(event, Mapping):
                                continue
                            self._validate_payload(event)
                            audio_data = event.get("data")
                            if not isinstance(audio_data, Mapping):
                                continue
                            encoded = audio_data.get("audio")
                            if not isinstance(encoded, str) or not encoded.strip():
                                continue
                            # With exclude_aggregated_audio=true, a status=2 packet may
                            # still contain an aggregate on older gateways. Never emit it twice.
                            if audio_data.get("status") == 2 and seen_chunk:
                                continue
                            try:
                                chunk = bytes.fromhex(encoded)
                            except ValueError as exc:
                                raise MiMoProtocolError(
                                    "Invalid hexadecimal MiniMax audio chunk"
                                ) from exc
                            if chunk:
                                seen_chunk = True
                                yield chunk
                        return
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise MiMoAPIError("MiniMax TTS transport failed", detail=str(exc)) from exc
                retry_delay = self._retry_delay(attempt)
            if retry_delay is not None:
                await self._sleep(retry_delay)
        raise AssertionError("retry loop always returns or raises")

    synthesize = stream_tts
    synthesize_stream = stream_tts
    tts = stream_tts
