from __future__ import annotations

from typing import Any

from .hybrid import HybridProvider
from .mimo import MiMoProvider
from .minimax import MiniMaxProvider


def create_ai_provider(settings: Any) -> MiMoProvider | MiniMaxProvider | HybridProvider:
    """Create a full-stack or capability-routed provider from settings."""
    llm_name = settings.llm_provider or settings.ai_provider
    asr_name = settings.asr_provider or settings.ai_provider
    tts_name = settings.tts_provider or settings.ai_provider
    providers: dict[str, MiMoProvider | MiniMaxProvider] = {}

    def get(name: str) -> MiMoProvider | MiniMaxProvider:
        if name not in providers:
            providers[name] = (
                MiniMaxProvider.from_settings(settings)
                if name == "minimax"
                else MiMoProvider.from_settings(settings)
            )
        return providers[name]

    if llm_name == asr_name == tts_name:
        return get(llm_name)
    return HybridProvider(get(llm_name), get(asr_name), get(tts_name))
