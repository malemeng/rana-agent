from __future__ import annotations

from fastapi import APIRouter, FastAPI, WebSocket

from ..services.voice import handle_voice_websocket

router = APIRouter(tags=["voice"])
voice_router = router


@router.websocket("/voice/ws")
@router.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket) -> None:
    await handle_voice_websocket(websocket)


def register_voice_routes(app: FastAPI, *, prefix: str = "/api/v1") -> None:
    app.include_router(router, prefix=prefix)
    if prefix:
        # Flutter's stable endpoint is root /ws/voice; retain versioned aliases.
        app.add_api_websocket_route("/ws/voice", voice_websocket)
