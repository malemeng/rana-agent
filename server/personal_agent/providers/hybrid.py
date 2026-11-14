from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class HybridProvider:
    """Route LLM, ASR and TTS independently while preserving one runtime contract."""

    provider_name = "hybrid"

    def __init__(self, llm: Any, asr: Any, tts: Any) -> None:
        self.llm_provider = llm
        self.asr_provider = asr
        self.tts_provider = tts
        self.display_name = (
            f"{llm.provider_name.upper()} LLM + {asr.provider_name.upper()} ASR + "
            f"{tts.provider_name.upper()} TTS"
        )
        self.model = llm.model
        self.asr_model = asr.asr_model
        self.tts_model = tts.tts_model
        self.voice = tts.voice

    @property
    def api_key(self) -> str | None:
        return self.llm_provider.api_key

    @property
    def missing_credentials(self) -> list[str]:
        missing: list[str] = []
        for provider in {
            id(p): p for p in (self.llm_provider, self.asr_provider, self.tts_provider)
        }.values():
            if provider.api_key:
                continue
            name = (
                "AGENT_MINIMAX_API_KEY"
                if provider.provider_name == "minimax"
                else "AGENT_MIMO_API_KEY"
            )
            if name not in missing:
                missing.append(name)
        return missing

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **options: Any,
    ) -> Any:
        return await self.llm_provider.complete(messages, tools=tools, model=model, **options)

    def stream_chat(
        self, messages: list[dict[str, Any]], model: str | None = None, **options: Any
    ) -> AsyncIterator[str]:
        return self.llm_provider.stream_chat(messages, model=model, **options)

    async def transcribe(
        self,
        audio_base64: str,
        audio_format: str = "wav",
        language: str | None = "auto",
        model: str | None = None,
    ) -> str:
        return await self.asr_provider.transcribe(
            audio_base64, audio_format=audio_format, language=language, model=model
        )

    def stream_tts(
        self,
        text: str,
        voice: str | None = None,
        model: str | None = None,
        instructions: str | None = None,
    ) -> AsyncIterator[bytes]:
        return self.tts_provider.stream_tts(
            text, voice=voice, model=model, instructions=instructions
        )

    async def close(self) -> None:
        seen: set[int] = set()
        for provider in (self.llm_provider, self.asr_provider, self.tts_provider):
            if id(provider) not in seen:
                seen.add(id(provider))
                await provider.close()
