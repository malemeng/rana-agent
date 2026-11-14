from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from ..config import get_settings
from ..providers import MiMoAPIError, MiMoError, MiMoProvider
from .deps import get_current_user

router = APIRouter(tags=["audio"])
audio_router = router


class ASRRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    audio_base64: str = Field(
        min_length=1,
        max_length=24 * 1024 * 1024,
        validation_alias=AliasChoices("audio_base64", "audio", "data"),
    )
    audio_format: Literal["wav", "mp3"] = Field(
        default="wav", validation_alias=AliasChoices("audio_format", "format")
    )
    language: Literal["auto", "zh", "en"] = "auto"


class TTSRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None
    instructions: str | None = None


def get_mimo_provider(request: Request) -> MiMoProvider:
    provider = getattr(request.app.state, "mimo_provider", None)
    if provider is None:
        provider = MiMoProvider.from_settings(get_settings())
        request.app.state.mimo_provider = provider
    return provider


def _http_error(exc: MiMoError) -> HTTPException:
    status_code = getattr(exc, "status_code", None)
    if isinstance(exc, MiMoAPIError) and status_code == 429:
        return HTTPException(status_code=429, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@router.post("/audio/asr")
async def transcribe_audio(
    payload: ASRRequest,
    request: Request,
    _current_user: object = Depends(get_current_user),
) -> dict[str, str]:
    provider = get_mimo_provider(request)
    try:
        text = await provider.transcribe(
            payload.audio_base64,
            audio_format=payload.audio_format,
            language=payload.language,
        )
    except MiMoError as exc:
        raise _http_error(exc) from exc
    return {"text": text}


@router.post("/audio/tts")
async def synthesize_audio(
    payload: TTSRequest,
    request: Request,
    _current_user: object = Depends(get_current_user),
) -> StreamingResponse:
    provider = get_mimo_provider(request)
    try:
        chunks = provider.stream_tts(
            payload.text,
            voice=payload.voice,
            instructions=payload.instructions,
        )
        return StreamingResponse(
            chunks,
            media_type="audio/pcm",
            headers={
                "X-Audio-Format": "pcm16",
                "X-Audio-Sample-Rate": "24000",
                "X-Audio-Channels": "1",
                "Cache-Control": "no-store",
            },
        )
    except MiMoError as exc:
        raise _http_error(exc) from exc


# Explicit aliases make the test endpoints easy to discover for ASR/TTS users.
router.add_api_route(
    "/audio/transcribe", transcribe_audio, methods=["POST"], include_in_schema=False
)
router.add_api_route(
    "/audio/synthesize", synthesize_audio, methods=["POST"], include_in_schema=False
)


def register_audio_routes(app: FastAPI, *, prefix: str = "/api/v1") -> None:
    app.include_router(router, prefix=prefix)
