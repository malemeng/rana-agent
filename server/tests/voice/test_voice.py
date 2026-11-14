from __future__ import annotations

import asyncio
import json
import struct
import wave
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from personal_agent.api.voice import register_voice_routes
from personal_agent.config import Settings
from personal_agent.db import dispose_database, init_database, make_engine, make_session_factory
from personal_agent.models import Conversation, Message, User
from personal_agent.security import create_access_token
from personal_agent.services.voice import (
    SentenceBuffer,
    VoiceFrame,
    VoiceSession,
    VoiceState,
    decode_frame,
    encode_frame,
    pcm16_to_wav,
)
from sqlalchemy import select


class FakeProvider:
    def __init__(self, chunks: list[str] | None = None) -> None:
        self.chunks = chunks or ["第一句。", "第二句！"]
        self.transcriptions: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []
        self.tts_calls: list[str] = []

    async def transcribe(self, audio_base64: str, **kwargs: Any) -> str:
        self.transcriptions.append({"audio": audio_base64, **kwargs})
        return "用户的话"

    async def stream_chat(self, messages: list[dict], model: str | None = None):
        self.llm_calls.append({"messages": messages, "model": model})
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk

    async def stream_tts(self, text: str, voice: str | None = None, **_kwargs: Any):
        self.tts_calls.append(text)
        yield f"{text}:0:{voice}".encode()
        await asyncio.sleep(0)
        yield f"{text}:1:{voice}".encode()


def make_session(
    provider: Any,
    *,
    session_factory: Any = None,
    conversation_id: str | None = None,
) -> tuple[VoiceSession, list[dict], list[bytes]]:
    messages: list[dict] = []
    frames: list[bytes] = []

    async def send_json(message: dict) -> None:
        messages.append(message)

    async def send_bytes(frame: bytes) -> None:
        frames.append(frame)

    return (
        VoiceSession(
            provider,
            "user-1",
            session_factory=session_factory,
            conversation_id=conversation_id,
            send_json=send_json,
            send_bytes=send_bytes,
            voice="Mia",
        ),
        messages,
        frames,
    )


async def run_turn(session: VoiceSession, turn_id: str = "turn-1") -> None:
    await session.start(turn_id)
    assert await session.accept_audio(b"\x01\x00\x02\x00", turn_id)
    await session.end(turn_id)
    await session.wait_idle()


def test_voice_frame_round_trip_and_validation() -> None:
    raw = encode_frame("audio", b"pcm", turn_id="turn-1", seq=7, sample_rate=16000)
    assert struct.unpack("!I", raw[:4])[0] == len(raw) - 4 - 3
    frame = decode_frame(raw)
    assert frame.kind == "audio"
    assert frame.turn_id == "turn-1"
    assert frame.seq == 7
    assert frame.payload == b"pcm"
    assert frame.header["sample_rate"] == 16000

    wrapped_header = encode_frame({"kind": "audio", "turn_id": "t", "seq": 2}, b"x")
    assert decode_frame(wrapped_header) == VoiceFrame(
        "audio",
        "t",
        2,
        b"x",
        {"kind": "audio", "turn_id": "t", "seq": 2},
    )
    with pytest.raises(ValueError):
        decode_frame(b"\x00\x00")
    with pytest.raises(ValueError):
        decode_frame(struct.pack("!I", 20) + b"{}")
    with pytest.raises(ValueError):
        decode_frame(struct.pack("!I", 100_000) + b"{}")


def test_pcm16_wrapper_and_sentence_splitter() -> None:
    wav = pcm16_to_wav(b"\x01\x00\x02\x00", 16000)
    with wave.open(__import__("io").BytesIO(wav), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 16000
        assert audio.readframes(2) == b"\x01\x00\x02\x00"

    splitter = SentenceBuffer()
    assert splitter.push("第一句。第") == ["第一句。"]
    assert splitter.push("二句！末尾") == ["第二句！"]
    assert splitter.finish() == "末尾"


@pytest.mark.asyncio
async def test_full_voice_pipeline_orders_sentences_and_audio() -> None:
    provider = FakeProvider()
    session, messages, raw_frames = make_session(provider)
    await run_turn(session)

    assert session.state is VoiceState.IDLE
    assert provider.transcriptions[0]["audio_format"] == "wav"
    assert provider.llm_calls[0]["messages"][-1] == {
        "role": "user",
        "content": "用户的话",
    }
    assert provider.tts_calls == ["第一句。", "第二句！"]
    sentence_messages = [item for item in messages if item["kind"] == "sentence"]
    assert [item["text"] for item in sentence_messages] == ["第一句。", "第二句！"]
    frames = [decode_frame(item) for item in raw_frames]
    assert [frame.seq for frame in frames] == [0, 1, 2, 3]
    assert all(frame.turn_id == "turn-1" for frame in frames)
    assert all(frame.header["format"] == "pcm16" for frame in frames)
    assert messages[-1] == {"kind": "state", "state": "idle", "turn_id": "turn-1"}
    assert any(item["kind"] == "done" for item in messages)


@pytest.mark.asyncio
async def test_stale_audio_is_ignored_and_empty_turn_finishes() -> None:
    provider = FakeProvider()
    session, messages, _frames = make_session(provider)
    await session.start("current")
    assert not await session.accept_audio(b"old", "stale")
    assert not await session.accept_audio(VoiceFrame("audio", "stale", 0, b"old", {}))
    await session.interrupt("stale")
    assert session.state is VoiceState.LISTENING
    await session.end("current")
    await session.wait_idle()
    assert session.state is VoiceState.IDLE
    assert any(
        item.get("kind") == "error" and item.get("error") == "Empty audio turn" for item in messages
    )


class BlockingTTSProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(["正在回答。"])
        self.started = asyncio.Event()

    async def stream_tts(self, text: str, voice: str | None = None, **_kwargs: Any):
        self.started.set()
        await asyncio.Event().wait()
        yield text.encode()


@pytest.mark.asyncio
async def test_barge_in_cancels_tts_and_isolates_new_turn() -> None:
    provider = BlockingTTSProvider()
    session, messages, frames = make_session(provider)
    await session.start("turn-old")
    await session.accept_audio(b"\x00\x00", "turn-old")
    await session.end("turn-old")
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    await session.start("turn-new")
    assert session.state is VoiceState.LISTENING
    assert session.active_turn_id == "turn-new"
    assert any(item == {"kind": "interrupted", "turn_id": "turn-old"} for item in messages)
    assert not await session.accept_audio(b"late", "turn-old")
    assert await session.accept_audio(b"\x00\x00", "turn-new")
    assert all(decode_frame(frame).turn_id == "turn-old" for frame in frames)
    await session.close()
    assert session.state is VoiceState.CLOSED


@pytest.mark.asyncio
async def test_voice_turns_reuse_conversation_and_message_models() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    factory = make_session_factory(engine)
    async with factory() as db:
        db.add(User(id="user-1", username="voice", password_hash="hash"))
        await db.flush()
        db.add(Conversation(id="conversation-1", user_id="user-1"))
        await db.commit()

    provider = FakeProvider(["持久化回答。"])
    session, _messages, _frames = make_session(
        provider, session_factory=factory, conversation_id="conversation-1"
    )
    await run_turn(session, "database-turn")

    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.conversation_id == "conversation-1")
                    .order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert [(row.role, row.content) for row in rows] == [
            ("user", "用户的话"),
            ("assistant", "持久化回答。"),
        ]
        assert all(row.turn_id == "database-turn" for row in rows)
        assert rows[-1].status == "completed"
    await dispose_database(engine)


@pytest.mark.asyncio
async def test_registered_websocket_alias_runs_binary_protocol() -> None:
    app = FastAPI()
    app.state.mimo_provider = FakeProvider(["WS 回答。"])
    app.state.settings = Settings(secret_key="voice-test-secret-0123456789012345")
    register_voice_routes(app)
    client = TestClient(app)
    token = create_access_token("user-1", secret_key="voice-test-secret-0123456789012345")

    with client.websocket_connect(f"/ws/voice?token={token}") as websocket:
        websocket.send_text(json.dumps({"kind": "start", "turn_id": "ws-turn"}))
        assert websocket.receive_json()["kind"] == "started"
        assert websocket.receive_json()["state"] == "listening"
        websocket.send_bytes(encode_frame("audio", b"\x00\x00", turn_id="ws-turn", seq=0))
        websocket.send_text(json.dumps({"kind": "end", "turn_id": "ws-turn"}))
        done = False
        audio_frames = 0
        while not done:
            incoming = websocket.receive()
            if incoming.get("bytes") is not None:
                frame = decode_frame(incoming["bytes"])
                assert frame.turn_id == "ws-turn"
                audio_frames += 1
            elif incoming.get("text") is not None:
                event = json.loads(incoming["text"])
                done = event.get("kind") == "done"
        assert audio_frames == 2
