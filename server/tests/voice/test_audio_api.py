from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from personal_agent.api.audio import register_audio_routes
from personal_agent.api.deps import get_current_user, get_db


class AudioProvider:
    def __init__(self) -> None:
        self.asr_kwargs: dict[str, Any] = {}
        self.tts_kwargs: dict[str, Any] = {}

    async def transcribe(self, audio_base64: str, **kwargs: Any) -> str:
        self.asr_kwargs = {"audio_base64": audio_base64, **kwargs}
        return "HTTP 识别"

    async def stream_tts(self, text: str, **kwargs: Any):
        self.tts_kwargs = {"text": text, **kwargs}
        yield b"\x01\x00"
        yield b"\x02\x00"


def test_audio_http_test_endpoints_use_injected_provider() -> None:
    app = FastAPI()
    provider = AudioProvider()
    app.state.mimo_provider = provider
    app.dependency_overrides[get_current_user] = lambda: object()
    register_audio_routes(app)
    client = TestClient(app)

    asr = client.post(
        "/api/v1/audio/asr",
        json={"audio_base64": "d2F2", "audio_format": "wav", "language": "zh"},
    )
    assert asr.status_code == 200
    assert asr.json() == {"text": "HTTP 识别"}
    assert provider.asr_kwargs == {
        "audio_base64": "d2F2",
        "audio_format": "wav",
        "language": "zh",
    }

    alias = client.post("/api/v1/audio/transcribe", json={"data": "bXAz", "format": "mp3"})
    assert alias.status_code == 200

    tts = client.post(
        "/api/v1/audio/tts",
        json={"text": "HTTP 合成", "voice": "Mia", "instructions": "轻声说"},
    )
    assert tts.status_code == 200
    assert tts.content == b"\x01\x00\x02\x00"
    assert tts.headers["content-type"].startswith("audio/pcm")
    assert tts.headers["x-audio-format"] == "pcm16"
    assert tts.headers["x-audio-sample-rate"] == "24000"
    assert provider.tts_kwargs == {
        "text": "HTTP 合成",
        "voice": "Mia",
        "instructions": "轻声说",
    }

    synthesize_alias = client.post("/api/v1/audio/synthesize", json={"text": "别名"})
    assert synthesize_alias.status_code == 200


def test_audio_endpoints_require_authentication() -> None:
    app = FastAPI()
    app.state.mimo_provider = AudioProvider()

    async def unused_db():
        yield None

    app.dependency_overrides[get_db] = unused_db
    register_audio_routes(app)
    client = TestClient(app)

    asr = client.post("/api/v1/audio/asr", json={"audio_base64": "d2F2"})
    tts = client.post("/api/v1/audio/tts", json={"text": "test"})
    assert asr.status_code == 401
    assert tts.status_code == 401
