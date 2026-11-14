from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import uuid
import wave
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from jwt import InvalidTokenError
from sqlalchemy import select

from ..models import Agent, Conversation, Message
from ..providers import MiMoProvider
from ..security import decode_access_token

MAX_HEADER_BYTES = 64 * 1024


class VoiceState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class VoiceFrame:
    kind: str
    turn_id: str | None
    seq: int
    payload: bytes
    header: dict[str, Any]

    @property
    def data(self) -> bytes:
        return self.payload


def encode_frame(
    kind: str | Mapping[str, Any],
    payload: bytes = b"",
    turn_id: str | None = None,
    seq: int = 0,
    **metadata: Any,
) -> bytes:
    """Encode 4-byte big-endian JSON length + header + binary payload."""

    if isinstance(kind, Mapping):
        header = dict(kind)
        header.update(metadata)
    else:
        header = {"kind": kind, "turn_id": turn_id, "seq": seq, **metadata}
    if not isinstance(header.get("kind"), str) or not header["kind"]:
        raise ValueError("Frame header requires a non-empty kind")
    header.setdefault("turn_id", None)
    header.setdefault("seq", 0)
    encoded = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_HEADER_BYTES:
        raise ValueError("Frame header is too large")
    return struct.pack("!I", len(encoded)) + encoded + payload


def decode_frame(data: bytes) -> VoiceFrame:
    if len(data) < 4:
        raise ValueError("Frame is missing its header length")
    header_length = struct.unpack("!I", data[:4])[0]
    if header_length > MAX_HEADER_BYTES:
        raise ValueError("Frame header is too large")
    header_end = 4 + header_length
    if header_length == 0 or len(data) < header_end:
        raise ValueError("Frame has a truncated header")
    try:
        header = json.loads(data[4:header_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Frame header must be UTF-8 JSON") from exc
    if not isinstance(header, dict):
        raise ValueError("Frame header must be a JSON object")
    kind = header.get("kind")
    turn_id = header.get("turn_id")
    seq = header.get("seq", 0)
    if not isinstance(kind, str) or not kind:
        raise ValueError("Frame header requires a non-empty kind")
    if turn_id is not None and not isinstance(turn_id, str):
        raise ValueError("turn_id must be a string or null")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise ValueError("seq must be a non-negative integer")
    return VoiceFrame(kind, turn_id, seq, data[header_end:], header)


def pcm16_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if len(pcm) % 2:
        raise ValueError("PCM16 payload must contain complete samples")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


class SentenceBuffer:
    """Incrementally split streamed text without losing punctuation."""

    _hard_terminators = frozenset("。！？!?；;\n")

    def __init__(self) -> None:
        self._buffer = ""

    def push(self, delta: str) -> list[str]:
        self._buffer += delta
        sentences: list[str] = []
        start = 0
        for index, char in enumerate(self._buffer):
            is_boundary = char in self._hard_terminators
            if char == ".":
                next_index = index + 1
                is_boundary = next_index == len(self._buffer) or self._buffer[next_index].isspace()
            if not is_boundary:
                continue
            sentence = self._buffer[start : index + 1].strip()
            if sentence:
                sentences.append(sentence)
            start = index + 1
        self._buffer = self._buffer[start:]
        return sentences

    def finish(self) -> str | None:
        sentence = self._buffer.strip()
        self._buffer = ""
        return sentence or None


async def _discard_json(_message: dict[str, Any]) -> None:
    return None


async def _discard_bytes(_frame: bytes) -> None:
    return None


class VoiceSession:
    """A cancellable, turn-isolated ASR -> LLM -> sentence TTS state machine."""

    def __init__(
        self,
        provider: Any,
        user_id: str,
        *,
        conversation_id: str | None = None,
        session_factory: Any = None,
        send_json: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        send_bytes: Callable[[bytes], Awaitable[None]] | None = None,
        system_prompt: str = "你是一个可靠、持续工作的个人助理。",
        model: str | None = None,
        voice: str | None = None,
        max_history: int = 40,
        max_audio_bytes: int = 16 * 1024 * 1024,
        task_manager: Any = None,
        interaction_mode: str = "auto",
    ) -> None:
        self.provider = provider
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.session_factory = session_factory
        self.send_json = send_json or _discard_json
        self.send_bytes = send_bytes or _discard_bytes
        self.system_prompt = system_prompt
        self.model = model
        self.voice = voice
        self.max_history = max_history
        self.max_audio_bytes = max_audio_bytes
        self.task_manager = task_manager
        self.interaction_mode = interaction_mode
        self.state = VoiceState.IDLE
        self.active_turn_id: str | None = None

        self._capture_turn_id: str | None = None
        self._capture_audio = bytearray()
        self._capture_format = "pcm16"
        self._capture_sample_rate = 16000
        self._generation = 0
        self._output_seq = 0
        self._task: asyncio.Task[None] | None = None
        self._memory_history: list[dict[str, str]] = []

    def _is_current(self, turn_id: str, generation: int) -> bool:
        return (
            self._generation == generation
            and self.active_turn_id == turn_id
            and self.state is not VoiceState.CLOSED
        )

    def _set_state(self, state: VoiceState) -> None:
        if self.state is not VoiceState.CLOSED:
            self.state = state

    async def _state_event(self, turn_id: str) -> None:
        await self.send_json({"kind": "state", "state": self.state.value, "turn_id": turn_id})

    async def start(self, turn_id: str | None = None, **metadata: Any) -> str:
        if self.state is VoiceState.CLOSED:
            raise RuntimeError("Voice session is closed")
        await self._cancel_active(notify=True)
        turn_id = turn_id or str(uuid.uuid4())
        conversation_id = metadata.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id:
            self.conversation_id = conversation_id
        audio_format = str(metadata.get("format", "pcm16")).lower()
        if audio_format not in {"pcm", "pcm16", "wav", "mp3"}:
            raise ValueError("format must be pcm16, wav or mp3")
        sample_rate = int(metadata.get("sample_rate", 16000))
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        mode = str(metadata.get("mode", self.interaction_mode)).lower()
        if mode not in {"auto", "chat", "task"}:
            raise ValueError("mode must be auto, chat or task")
        self.interaction_mode = mode
        self._capture_turn_id = turn_id
        self.active_turn_id = turn_id
        self._capture_audio = bytearray()
        self._capture_format = audio_format
        self._capture_sample_rate = sample_rate
        self._set_state(VoiceState.LISTENING)
        await self.send_json({"kind": "started", "turn_id": turn_id})
        await self._state_event(turn_id)
        return turn_id

    async def accept_audio(
        self,
        frame: VoiceFrame | bytes,
        turn_id: str | None = None,
    ) -> bool:
        if isinstance(frame, VoiceFrame):
            turn_id = frame.turn_id
            if frame.kind not in {"audio", "pcm", "pcm16", "input_audio"}:
                raise ValueError(f"Unsupported binary frame kind: {frame.kind}")
            payload = frame.payload
        else:
            payload = frame
        if (
            self.state is not VoiceState.LISTENING
            or self._capture_turn_id is None
            or (turn_id is not None and turn_id != self._capture_turn_id)
        ):
            return False
        if len(self._capture_audio) + len(payload) > self.max_audio_bytes:
            await self.interrupt(turn_id)
            await self.send_json(
                {
                    "kind": "error",
                    "turn_id": turn_id,
                    "error": f"Audio turn exceeds {self.max_audio_bytes} byte limit",
                }
            )
            return False
        self._capture_audio.extend(payload)
        return True

    handle_frame = accept_audio

    async def end(self, turn_id: str | None = None) -> asyncio.Task[None] | None:
        if self.state is not VoiceState.LISTENING or self._capture_turn_id is None:
            return None
        if turn_id is not None and turn_id != self._capture_turn_id:
            return None
        turn_id = self._capture_turn_id
        audio = bytes(self._capture_audio)
        audio_format = self._capture_format
        sample_rate = self._capture_sample_rate
        self._capture_turn_id = None
        self._capture_audio.clear()
        self.active_turn_id = turn_id
        if not audio:
            self.active_turn_id = None
            self._set_state(VoiceState.IDLE)
            await self.send_json({"kind": "error", "turn_id": turn_id, "error": "Empty audio turn"})
            return None
        self._set_state(VoiceState.TRANSCRIBING)
        self._output_seq = 0
        await self._state_event(turn_id)
        generation = self._generation
        task = asyncio.create_task(
            self._run_turn(turn_id, generation, audio, audio_format, sample_rate)
        )
        self._task = task
        return task

    def _should_create_goal(self, transcript: str) -> bool:
        if self.task_manager is None or self.interaction_mode == "chat":
            return False
        if self.interaction_mode == "task":
            return True
        normalized = transcript.strip().lower()
        explicit = (
            "后台执行",
            "后台运行",
            "创建任务",
            "新建任务",
            "创建目标",
            "执行任务",
            "每天",
            "每周",
            "每小时",
            "提醒我",
            "持续监控",
            "定期检查",
            "remind me",
            "every day",
            "schedule a task",
        )
        return any(keyword in normalized for keyword in explicit)

    async def _handle_goal_turn(
        self,
        transcript: str,
        turn_id: str,
        generation: int,
        assistant_message_id: str | None,
        voice: str | None,
    ) -> bool:
        if not self._should_create_goal(transcript):
            return False
        self._set_state(VoiceState.THINKING)
        await self._state_event(turn_id)
        goal, task = await self.task_manager.create_goal(
            self.user_id,
            transcript[:80],
            transcript,
            idempotency_key=f"voice:{turn_id}",
        )
        content = "已创建后台任务，可以在任务中心查看进度。"
        await self.send_json(
            {
                "kind": "task_created",
                "turn_id": turn_id,
                "goal_id": goal.id,
                "task_id": task.id,
                "status": task.status,
            }
        )
        await self.send_json({"kind": "text", "turn_id": turn_id, "delta": content})
        await self._speak_sentence(content, turn_id, generation, 0, voice)
        await self._finish_turn(assistant_message_id, content, "completed")
        if not self._is_current(turn_id, generation):
            return True
        self.active_turn_id = None
        self._task = None
        self._set_state(VoiceState.IDLE)
        await self.send_json(
            {
                "kind": "done",
                "turn_id": turn_id,
                "text": content,
                "goal_id": goal.id,
                "task_id": task.id,
            }
        )
        await self._state_event(turn_id)
        return True

    async def _run_turn(
        self,
        turn_id: str,
        generation: int,
        audio: bytes,
        audio_format: str,
        sample_rate: int,
    ) -> None:
        assistant_message_id: str | None = None
        response_parts: list[str] = []
        try:
            if audio_format in {"pcm", "pcm16"}:
                audio = pcm16_to_wav(audio, sample_rate)
                audio_format = "wav"
            transcript = await self.provider.transcribe(
                base64.b64encode(audio).decode("ascii"),
                audio_format=audio_format,
                language="auto",
            )
            transcript = transcript.strip()
            if not self._is_current(turn_id, generation):
                return
            if not transcript:
                raise ValueError("ASR returned an empty transcript")
            await self.send_json({"kind": "transcript", "turn_id": turn_id, "text": transcript})

            messages, assistant_message_id, model, voice = await self._begin_turn(
                transcript, turn_id
            )
            if not self._is_current(turn_id, generation):
                return
            if await self._handle_goal_turn(
                transcript, turn_id, generation, assistant_message_id, voice
            ):
                return
            self._set_state(VoiceState.THINKING)
            await self._state_event(turn_id)

            sentences = SentenceBuffer()
            sentence_index = 0
            async for delta in self.provider.stream_chat(messages, model=model):
                if not self._is_current(turn_id, generation):
                    return
                if not delta:
                    continue
                response_parts.append(delta)
                await self.send_json({"kind": "text", "turn_id": turn_id, "delta": delta})
                for sentence in sentences.push(delta):
                    await self._speak_sentence(sentence, turn_id, generation, sentence_index, voice)
                    sentence_index += 1
            remainder = sentences.finish()
            if remainder and self._is_current(turn_id, generation):
                await self._speak_sentence(remainder, turn_id, generation, sentence_index, voice)

            content = "".join(response_parts)
            if not self._is_current(turn_id, generation):
                return
            await self._finish_turn(assistant_message_id, content, "completed")
            self.active_turn_id = None
            self._task = None
            self._set_state(VoiceState.IDLE)
            await self.send_json({"kind": "done", "turn_id": turn_id, "text": content})
            await self._state_event(turn_id)
        except asyncio.CancelledError:
            with suppress(Exception):
                await self._finish_turn(assistant_message_id, "".join(response_parts), "cancelled")
            raise
        except Exception as exc:
            with suppress(Exception):
                await self._finish_turn(assistant_message_id, "".join(response_parts), "error")
            if self._is_current(turn_id, generation):
                self.active_turn_id = None
                self._task = None
                self._set_state(VoiceState.IDLE)
                await self.send_json({"kind": "error", "turn_id": turn_id, "error": str(exc)})
                await self._state_event(turn_id)

    async def _speak_sentence(
        self,
        sentence: str,
        turn_id: str,
        generation: int,
        sentence_index: int,
        voice: str | None,
    ) -> None:
        if not self._is_current(turn_id, generation):
            return
        self._set_state(VoiceState.SPEAKING)
        await self._state_event(turn_id)
        await self.send_json(
            {
                "kind": "sentence",
                "turn_id": turn_id,
                "index": sentence_index,
                "text": sentence,
            }
        )
        async for audio in self.provider.stream_tts(sentence, voice=voice or self.voice):
            if not self._is_current(turn_id, generation):
                return
            if not audio:
                continue
            await self.send_bytes(
                encode_frame(
                    "audio",
                    audio,
                    turn_id=turn_id,
                    seq=self._output_seq,
                    sentence_index=sentence_index,
                    format="pcm16",
                    sample_rate=24000,
                    channels=1,
                )
            )
            self._output_seq += 1

    async def _begin_turn(
        self, transcript: str, turn_id: str
    ) -> tuple[list[dict[str, str]], str | None, str | None, str | None]:
        model = self.model
        voice = self.voice
        system_prompt = self.system_prompt
        if self.session_factory is None:
            history = list(self._memory_history)
            self._memory_history.append({"role": "user", "content": transcript})
            messages = [{"role": "system", "content": system_prompt}, *history]
            messages.append({"role": "user", "content": transcript})
            return messages, None, model, voice

        async with self.session_factory() as db:
            conversation = None
            if self.conversation_id:
                conversation = await db.get(Conversation, self.conversation_id)
                if conversation is None or conversation.user_id != self.user_id:
                    raise ValueError("Conversation does not belong to this user")
            else:
                conversation = Conversation(user_id=self.user_id)
                db.add(conversation)
                await db.flush()
                self.conversation_id = conversation.id

            if conversation.agent_id:
                agent = await db.get(Agent, conversation.agent_id)
                if agent is not None:
                    system_prompt = agent.system_prompt
                    model = agent.model or model
                    voice = agent.voice or voice

            result = await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.status == "completed",
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(self.max_history)
            )
            history_rows = list(reversed(result.scalars().all()))
            history = [
                {"role": row.role, "content": row.content} for row in history_rows if row.content
            ]
            user_message = Message(
                conversation_id=conversation.id,
                user_id=self.user_id,
                role="user",
                content=transcript,
                status="completed",
                turn_id=turn_id,
            )
            assistant_message = Message(
                conversation_id=conversation.id,
                user_id=self.user_id,
                role="assistant",
                content="",
                status="streaming",
                turn_id=turn_id,
            )
            db.add_all([user_message, assistant_message])
            await db.commit()
            assistant_message_id = assistant_message.id

        messages = [{"role": "system", "content": system_prompt}, *history]
        messages.append({"role": "user", "content": transcript})
        return messages, assistant_message_id, model, voice

    async def _finish_turn(
        self, assistant_message_id: str | None, content: str, status: str
    ) -> None:
        if assistant_message_id is None:
            if self.session_factory is None and content:
                self._memory_history.append({"role": "assistant", "content": content})
            return
        async with self.session_factory() as db:
            message = await db.get(Message, assistant_message_id)
            if message is not None:
                message.content = content
                message.status = status
                await db.commit()

    async def _cancel_active(self, *, notify: bool) -> None:
        old_turn = self.active_turn_id or self._capture_turn_id
        task = self._task
        self._task = None
        self._generation += 1
        self.active_turn_id = None
        self._capture_turn_id = None
        self._capture_audio.clear()
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if notify and old_turn:
            await self.send_json({"kind": "interrupted", "turn_id": old_turn})

    async def interrupt(self, turn_id: str | None = None) -> None:
        if turn_id is not None and turn_id not in {
            self.active_turn_id,
            self._capture_turn_id,
        }:
            return
        await self._cancel_active(notify=True)
        if self.state is not VoiceState.CLOSED:
            self._set_state(VoiceState.IDLE)
            if turn_id:
                await self._state_event(turn_id)

    cancel = interrupt

    async def wait_idle(self) -> None:
        task = self._task
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

    async def close(self) -> None:
        await self._cancel_active(notify=False)
        self.state = VoiceState.CLOSED


def _websocket_user_id(websocket: WebSocket) -> str:
    token = websocket.query_params.get("token")
    authorization = websocket.headers.get("Authorization", "")
    if not token and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    settings = getattr(websocket.app.state, "settings", None)
    secret_key = getattr(settings, "secret_key", None)
    if token:
        if not secret_key:
            raise ValueError("JWT secret key is not configured")
        return str(decode_access_token(token, secret_key=secret_key)["sub"])
    if secret_key:
        raise ValueError("Missing bearer token")

    state_user_id = getattr(websocket.state, "user_id", None)
    if state_user_id:
        return str(state_user_id)
    user = getattr(websocket.state, "user", None)
    if user is not None and getattr(user, "id", None):
        return str(user.id)
    return websocket.query_params.get("user_id", "default")


async def handle_voice_websocket(
    websocket: WebSocket,
    *,
    provider: Any = None,
    session_factory: Any = None,
    user_id: str | None = None,
) -> None:
    """WebSocket adapter; main only needs to mount the containing router."""

    await websocket.accept()
    if provider is None:
        provider = getattr(websocket.app.state, "mimo_provider", None)
    if provider is None:
        from ..config import get_settings

        provider = MiMoProvider.from_settings(get_settings())
        websocket.app.state.mimo_provider = provider
    if session_factory is None:
        session_factory = getattr(websocket.app.state, "session_factory", None)
    if session_factory is None:
        session_factory = getattr(websocket.app.state, "db_session_factory", None)

    try:
        resolved_user_id = user_id or _websocket_user_id(websocket)
    except (InvalidTokenError, ValueError):
        await websocket.close(code=1008)
        return

    session = VoiceSession(
        provider,
        resolved_user_id,
        conversation_id=websocket.query_params.get("conversation_id"),
        session_factory=session_factory,
        send_json=websocket.send_json,
        send_bytes=websocket.send_bytes,
        task_manager=getattr(websocket.app.state, "task_manager", None),
    )
    try:
        while True:
            incoming = await websocket.receive()
            if incoming["type"] == "websocket.disconnect":
                break
            if incoming.get("text") is not None:
                try:
                    control = json.loads(incoming["text"])
                    if not isinstance(control, dict):
                        raise ValueError("Control message must be a JSON object")
                    kind = control.get("kind") or control.get("type")
                    turn_id = control.get("turn_id")
                    if kind == "start":
                        metadata = {
                            key: value
                            for key, value in control.items()
                            if key not in {"kind", "type", "turn_id"}
                        }
                        await session.start(turn_id=turn_id, **metadata)
                    elif kind == "end":
                        await session.end(turn_id=turn_id)
                    elif kind in {"interrupt", "cancel"}:
                        await session.interrupt(turn_id=turn_id)
                    else:
                        raise ValueError(f"Unknown control kind: {kind}")
                except (ValueError, json.JSONDecodeError) as exc:
                    await websocket.send_json({"kind": "error", "error": str(exc)})
            elif incoming.get("bytes") is not None:
                try:
                    frame = decode_frame(incoming["bytes"])
                    await session.accept_audio(frame)
                except ValueError as exc:
                    await websocket.send_json({"kind": "error", "error": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
