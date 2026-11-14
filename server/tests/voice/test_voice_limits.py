from personal_agent.services.voice import VoiceSession, VoiceState


async def test_voice_audio_limit_cancels_current_turn() -> None:
    messages = []
    session = VoiceSession(
        None,
        "user",
        max_audio_bytes=4,
        send_json=lambda message: _append(messages, message),
    )
    turn_id = await session.start("limited")
    assert await session.accept_audio(b"1234", turn_id) is True
    assert await session.accept_audio(b"5", turn_id) is False
    assert session.state is VoiceState.IDLE
    assert messages[-1]["kind"] == "error"
    assert "limit" in messages[-1]["error"]


async def _append(messages, message) -> None:
    messages.append(message)
