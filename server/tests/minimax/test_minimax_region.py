from personal_agent.config import Settings
from personal_agent.providers import MiniMaxProvider, create_ai_provider


def test_minimax_cn_region_uses_cn_for_llm_asr_tts() -> None:
    provider = create_ai_provider(
        Settings(
            ai_provider="minimax",
            minimax_region="cn",
            minimax_api_key="test-key",
        )
    )
    assert isinstance(provider, MiniMaxProvider)
    assert provider.base_url == "https://api.minimax.cn/v1"
    assert provider.endpoint == "https://api.minimax.cn/v1/chat/completions"
    assert provider.asr_endpoint == "https://api.minimax.cn/v1/speech_to_text"
    assert provider.tts_endpoint == "https://api.minimax.cn/v1/t2a_v2"


def test_minimax_custom_base_url_is_preserved() -> None:
    provider = create_ai_provider(
        Settings(
            ai_provider="minimax",
            minimax_region="cn",
            minimax_base_url="https://mini-gateway.local/v1",
        )
    )
    assert provider.base_url == "https://mini-gateway.local/v1"
