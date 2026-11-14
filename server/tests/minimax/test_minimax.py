from __future__ import annotations

import base64
import json

import httpx
import pytest
from personal_agent.config import Settings
from personal_agent.providers import MiMoProvider, MiniMaxProvider, create_ai_provider
from personal_agent.providers.mimo import MiMoAPIError


def sse(events: list[dict]) -> bytes:
    return (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode() + b"data: [DONE]\n\n"
    )


async def test_chat_openai_tools_and_reasoning() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.minimax.io/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "MiniMax-M3.1-Flash"
        assert body["reasoning_effort"] == "medium"
        assert body["tools"][0]["function"]["name"] == "workspace_write"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "准备写入",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "workspace_write", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"total_tokens": 12},
            },
        )

    provider = MiniMaxProvider(
        api_key="test-key", transport=httpx.MockTransport(handler), max_retries=0
    )
    result = await provider.complete(
        [{"role": "user", "content": "test"}],
        tools=[{"type": "function", "function": {"name": "workspace_write", "parameters": {}}}],
    )
    assert result.content == "准备写入"
    assert result.tool_calls[0]["id"] == "call-1"
    assert result.usage["total_tokens"] == 12
    await provider.close()


async def test_stream_chat_ignores_reasoning_channel() -> None:
    content = sse(
        [
            {"choices": [{"delta": {"reasoning_content": "thinking"}}]},
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " 世界"}}]},
        ]
    )
    provider = MiniMaxProvider(
        api_key="key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)),
        max_retries=0,
    )
    chunks = [chunk async for chunk in provider.stream_chat([{"role": "user", "content": "hi"}])]
    assert chunks == ["Hello", " 世界"]
    await provider.close()


async def test_asr_uses_multipart_wav() -> None:
    audio = b"wav-test-data"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/speech_to_text"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        assert "language" not in request.headers
        assert b'name="model"' in request.content
        assert b"asr-1.0" in request.content
        assert audio in request.content
        return httpx.Response(200, json={"text": "你好，世界", "duration": 1.2})

    provider = MiniMaxProvider(api_key="key", transport=httpx.MockTransport(handler), max_retries=0)
    result = await provider.transcribe(base64.b64encode(audio).decode(), language="auto")
    assert result == "你好，世界"
    await provider.close()


async def test_tts_streams_pcm24k_hex_without_aggregate_duplicate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/t2a_v2"
        assert body["model"] == "speech-2.8-turbo"
        assert body["audio_setting"] == {
            "sample_rate": 24000,
            "format": "pcm",
            "channel": 1,
        }
        assert body["stream_options"]["exclude_aggregated_audio"] is True
        assert body["voice_setting"]["emotion"] == "calm"
        return httpx.Response(
            200,
            content=sse(
                [
                    {"data": {"audio": "dead", "status": 1}},
                    {"data": {"audio": "beef", "status": 1}},
                    {"data": {"audio": "deadbeef", "status": 2}},
                ]
            ),
        )

    provider = MiniMaxProvider(api_key="key", transport=httpx.MockTransport(handler), max_retries=0)
    chunks = [chunk async for chunk in provider.stream_tts("你好", instructions="calm")]
    assert chunks == [bytes.fromhex("dead"), bytes.fromhex("beef")]
    await provider.close()


async def test_tts_accepts_final_only_packet() -> None:
    provider = MiniMaxProvider(
        api_key="key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=sse([{"data": {"audio": "00ff", "status": 2}}]))
        ),
        max_retries=0,
    )
    assert [chunk async for chunk in provider.stream_tts("test")] == [b"\x00\xff"]
    await provider.close()


async def test_retry_and_business_rate_limit() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "rate limit"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = MiniMaxProvider(api_key="key", transport=httpx.MockTransport(handler), sleeper=sleep)
    result = await provider.complete([{"role": "user", "content": "test"}])
    assert result.content == "ok"
    assert sleeps == [0.5]
    await provider.close()

    provider = MiniMaxProvider(
        api_key="key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"base_resp": {"status_code": 1002, "status_msg": "rate limit"}},
            )
        ),
        max_retries=0,
    )
    with pytest.raises(MiMoAPIError) as error:
        await provider.complete([{"role": "user", "content": "test"}])
    assert error.value.status_code == 429
    await provider.close()


def test_provider_factory_selects_minimax_or_mimo() -> None:
    settings = Settings(ai_provider="minimax", minimax_api_key="mini-key")
    selected = create_ai_provider(settings)
    assert isinstance(selected, MiniMaxProvider)
    assert selected.model == "MiniMax-M3.1-Flash"
    assert selected.api_key == "mini-key"
    default = create_ai_provider(Settings())
    assert isinstance(default, MiMoProvider)
    assert not isinstance(default, MiniMaxProvider)
