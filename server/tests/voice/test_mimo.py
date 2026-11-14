from __future__ import annotations

import base64
import json

import httpx
import pytest
from personal_agent.providers import (
    ChatResult,
    MiMoAPIError,
    MiMoProtocolError,
    MiMoProvider,
)


def provider_for(
    handler,
    *,
    max_retries: int = 0,
    sleeps: list[float] | None = None,
) -> MiMoProvider:
    async def sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MiMoProvider(
        "test-key",
        client=client,
        max_retries=max_retries,
        backoff_factor=0.25,
        sleeper=sleep,
    )


@pytest.mark.asyncio
async def test_complete_uses_official_contract_and_parses_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.xiaomimimo.com/v1/chat/completions"
        assert request.headers["api-key"] == "test-key"
        body = json.loads(request.content)
        assert body == {
            "model": "mimo-v2.6-pro",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": False,
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "clock", "parameters": {"type": "object"}},
                }
            ],
            "temperature": 0.2,
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "完成",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "clock", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    provider = provider_for(handler)
    result = await provider.complete(
        [{"role": "user", "content": "你好"}],
        tools=[
            {
                "type": "function",
                "function": {"name": "clock", "parameters": {"type": "object"}},
            }
        ],
        model="mimo-v2.6-pro",
        temperature=0.2,
    )
    assert isinstance(result, ChatResult)
    assert result.content == "完成"
    assert result.tool_calls[0]["function"]["name"] == "clock"
    assert result.usage["prompt_tokens"] == 4
    await provider.close()


@pytest.mark.asyncio
async def test_complete_retries_429_and_5xx_with_backoff() -> None:
    statuses = iter([429, 503, 200])
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = next(statuses)
        if status != 200:
            return httpx.Response(
                status,
                json={"error": {"message": "try again"}},
                headers={"Retry-After": "0.125"} if status == 429 else None,
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    provider = provider_for(handler, max_retries=2, sleeps=sleeps)
    result = await provider.complete([{"role": "user", "content": "go"}])
    assert result.content == "ok"
    assert calls == 3
    assert sleeps == [0.125, 0.5]


@pytest.mark.asyncio
async def test_complete_does_not_retry_client_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "bad input"}})

    provider = provider_for(handler, max_retries=3)
    with pytest.raises(MiMoAPIError) as caught:
        await provider.complete([{"role": "user", "content": "bad"}])
    assert caught.value.status_code == 400
    assert calls == 1


@pytest.mark.asyncio
async def test_stream_chat_decodes_sse_and_retries_initial_response() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": "temporary"})
        content = (
            ": keepalive\n\n"
            'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
            'data: {"choices":[],"usage":{"completion_tokens":2}}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        return httpx.Response(200, content=content)

    provider = provider_for(handler, max_retries=1, sleeps=sleeps)
    chunks = [
        chunk
        async for chunk in provider.stream_chat(
            [{"role": "user", "content": "hello"}], model="mimo-v2.6-flash"
        )
    ]
    assert chunks == ["你", "好"]
    assert calls == 2
    assert sleeps == [0.25]


@pytest.mark.asyncio
async def test_asr_accepts_wav_and_mp3_base64() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "识别结果"}}]})

    provider = provider_for(handler)
    text = await provider.transcribe(
        base64.b64encode(b"wav").decode(), audio_format="wav", language="zh"
    )
    assert text == "识别结果"
    await provider.asr(base64.b64encode(b"mp3").decode(), audio_format="mp3")
    first_audio = requests[0]["messages"][0]["content"][0]["input_audio"]
    second_audio = requests[1]["messages"][0]["content"][0]["input_audio"]
    assert first_audio["format"] == "wav"
    assert second_audio["format"] == "mp3"
    assert base64.b64decode(first_audio["data"]) == b"wav"
    assert requests[0]["model"] == "mimo-v2.5-asr"
    assert requests[0]["asr_options"] == {"language": "zh"}


@pytest.mark.asyncio
async def test_tts_parses_delta_audio_and_tolerant_variants() -> None:
    expected = [b"\x01\x00", b"\x02\x00", b"\x03\x00", b"\x04\x00"]

    def encoded(value: bytes) -> str:
        return base64.b64encode(value).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "mimo-v2.5-tts"
        assert body["audio"] == {"voice": "Mia", "format": "pcm16"}
        assert body["messages"] == [
            {"role": "user", "content": "平静地说"},
            {"role": "assistant", "content": "你好"},
        ]
        assert body["stream"] is True
        events = [
            {"choices": [{"delta": {"audio": {"data": encoded(expected[0])}}}]},
            {"choices": [{"delta": {"audio": encoded(expected[1])}}]},
            {"audio": {"data": f"data:audio/pcm;base64,{encoded(expected[2])}"}},
            {"data": encoded(expected[3])},
        ]
        content = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        content += "data: [DONE]\n\n"
        return httpx.Response(200, content=content.encode())

    provider = provider_for(handler)
    chunks = [
        chunk async for chunk in provider.stream_tts("你好", voice="Mia", instructions="平静地说")
    ]
    assert chunks == expected


@pytest.mark.asyncio
async def test_tts_rejects_invalid_audio_base64() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'data: {"choices":[{"delta":{"audio":{"data":"%%%"}}}]}\n\n',
        )

    provider = provider_for(handler)
    with pytest.raises(MiMoProtocolError):
        _ = [chunk async for chunk in provider.stream_tts("bad")]


@pytest.mark.asyncio
async def test_streaming_final_error_status_raises_mimo_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": {"message": "limited"}})

    provider = provider_for(handler)
    with pytest.raises(MiMoAPIError) as chat_error:
        _ = [chunk async for chunk in provider.stream_chat([{"role": "user", "content": "go"}])]
    assert chat_error.value.status_code == 429

    with pytest.raises(MiMoAPIError) as tts_error:
        _ = [chunk async for chunk in provider.stream_tts("go")]
    assert tts_error.value.status_code == 429
    assert calls == 2
