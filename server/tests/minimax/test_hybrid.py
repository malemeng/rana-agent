from __future__ import annotations

from personal_agent.config import Settings
from personal_agent.providers import HybridProvider, create_ai_provider
from personal_agent.providers.mimo import MiMoProvider
from personal_agent.providers.minimax import MiniMaxProvider


def test_factory_supports_minimax_llm_with_mimo_asr() -> None:
    settings = Settings(
        ai_provider="mimo",
        llm_provider="minimax",
        asr_provider="mimo",
        tts_provider="mimo",
        minimax_api_key="mini-key",
        mimo_api_key="mimo-key",
    )
    provider = create_ai_provider(settings)
    assert isinstance(provider, HybridProvider)
    assert isinstance(provider.llm_provider, MiniMaxProvider)
    assert isinstance(provider.asr_provider, MiMoProvider)
    assert provider.tts_provider is provider.asr_provider
    assert provider.model == "MiniMax-M3.1-Flash"
    assert provider.asr_model == "mimo-v2.5-asr"
    assert provider.missing_credentials == []


class FakeProvider:
    def __init__(self, name: str, api_key: str | None = "key") -> None:
        self.provider_name = name
        self.api_key = api_key
        self.model = f"{name}-llm"
        self.asr_model = f"{name}-asr"
        self.tts_model = f"{name}-tts"
        self.voice = "voice"
        self.closed = 0

    async def complete(self, messages, tools=None, model=None, **options):
        return {"provider": self.provider_name, "options": options}

    async def stream_chat(self, messages, model=None, **options):
        yield self.provider_name

    async def transcribe(self, audio_base64, audio_format="wav", language="auto", model=None):
        return f"{self.provider_name}:{audio_base64}"

    async def stream_tts(self, text, voice=None, model=None, instructions=None):
        yield self.provider_name.encode()

    async def close(self) -> None:
        self.closed += 1


async def test_hybrid_delegates_and_closes_unique_providers() -> None:
    llm = FakeProvider("minimax")
    asr = FakeProvider("mimo")
    tts = FakeProvider("minimax")
    provider = HybridProvider(llm, asr, tts)
    assert (await provider.complete([{"role": "user", "content": "hi"}]))["provider"] == "minimax"
    assert [chunk async for chunk in provider.stream_chat([])] == ["minimax"]
    assert await provider.transcribe("YXVkaW8=") == "mimo:YXVkaW8="
    assert [chunk async for chunk in provider.stream_tts("你好")] == [b"minimax"]
    await provider.close()
    assert (llm.closed, asr.closed, tts.closed) == (1, 1, 1)


def test_hybrid_reports_each_missing_credential_once() -> None:
    llm = FakeProvider("minimax", api_key=None)
    mimo = FakeProvider("mimo", api_key=None)
    provider = HybridProvider(llm, mimo, mimo)
    assert provider.missing_credentials == ["AGENT_MINIMAX_API_KEY", "AGENT_MIMO_API_KEY"]
