"""Minimal real MiMo LLM -> TTS -> ASR verification; never prints credentials."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import wave

from personal_agent.config import get_settings
from personal_agent.providers.mimo import MiMoProvider


async def main() -> None:
    provider = MiMoProvider.from_settings(get_settings())
    try:
        answer = await provider.complete([{"role": "user", "content": "只回复四个字：联调成功"}])
        pcm = b"".join([chunk async for chunk in provider.stream_tts("你好，这是语音联调测试。")])
        if not pcm:
            raise RuntimeError("TTS returned empty audio")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(pcm)
        transcript = await provider.transcribe(
            base64.b64encode(buffer.getvalue()).decode("ascii"),
            audio_format="wav",
            language="zh",
        )
        print(
            json.dumps(
                {
                    "llm": answer.content,
                    "tts_pcm_bytes": len(pcm),
                    "asr": transcript,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
