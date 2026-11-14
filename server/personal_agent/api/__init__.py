"""HTTP and WebSocket route registration for the Rana Agent API."""

from fastapi import FastAPI

from .events import router as events_router
from .routers import rest_routers


def include_rest_routes(app: FastAPI, prefix: str = "/api/v1") -> None:
    """Register all REST routes and both supported event WebSocket paths."""
    normalized_prefix = prefix.rstrip("/")
    for router in rest_routers:
        app.include_router(router, prefix=normalized_prefix)
    app.include_router(events_router, prefix=normalized_prefix)
    # Flutter uses the root endpoint; retain the versioned alias for other clients.
    app.include_router(events_router)


__all__ = ["include_rest_routes"]
