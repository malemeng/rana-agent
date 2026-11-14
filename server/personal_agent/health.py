from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import text

from . import __version__

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": __version__,
        "time": datetime.now(UTC).isoformat(),
    }


@router.get("/readyz")
async def ready(request: Request) -> dict[str, str]:
    async with request.app.state.session_factory() as session:
        await session.execute(text("SELECT 1"))
    manager = getattr(request.app.state, "task_manager", None)
    if manager is not None and hasattr(manager, "running") and not manager.running:
        return {"status": "starting"}
    return {"status": "ready"}


@router.get("/")
async def root() -> dict[str, str]:
    return {"name": "Rana Agent", "version": __version__, "docs": "/docs"}
