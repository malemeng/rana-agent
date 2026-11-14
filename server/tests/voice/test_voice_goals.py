from types import SimpleNamespace

from personal_agent.services.voice import VoiceSession

from .test_voice import FakeProvider, run_turn


class FakeTaskManager:
    def __init__(self) -> None:
        self.calls = []

    async def create_goal(self, user_id, title, input, idempotency_key=None):
        self.calls.append(
            {
                "user_id": user_id,
                "title": title,
                "input": input,
                "idempotency_key": idempotency_key,
            }
        )
        return SimpleNamespace(id="goal-1"), SimpleNamespace(id="task-1", status="queued")


async def test_explicit_voice_goal_runs_in_background_without_chat_completion() -> None:
    provider = FakeProvider()

    async def transcribe(*args, **kwargs):
        return "每天九点整理日程和邮件"

    provider.transcribe = transcribe
    manager = FakeTaskManager()
    messages = []
    frames = []

    async def send_json(value):
        messages.append(value)

    async def send_bytes(value):
        frames.append(value)

    session = VoiceSession(
        provider,
        "user-1",
        task_manager=manager,
        send_json=send_json,
        send_bytes=send_bytes,
    )
    await run_turn(session)
    assert len(manager.calls) == 1
    assert manager.calls[0]["idempotency_key"] == "voice:turn-1"
    assert provider.llm_calls == []
    assert any(item.get("kind") == "task_created" for item in messages)
    assert any(item.get("kind") == "done" and item.get("task_id") == "task-1" for item in messages)
    assert provider.tts_calls == ["已创建后台任务，可以在任务中心查看进度。"]


async def test_voice_chat_mode_never_creates_background_goal() -> None:
    provider = FakeProvider()
    manager = FakeTaskManager()
    session = VoiceSession(provider, "user-1", task_manager=manager, interaction_mode="chat")
    await run_turn(session)
    assert manager.calls == []
    assert len(provider.llm_calls) == 1
