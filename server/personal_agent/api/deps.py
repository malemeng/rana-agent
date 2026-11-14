"""Shared FastAPI dependencies and owner-scoped persistence helpers."""

from collections.abc import AsyncIterator
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..config import Settings, get_settings
from ..models import User
from ..security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """Prefer the application instance settings, with an environment fallback."""
    return getattr(request.app.state, "settings", None) or get_settings()


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database session factory is not configured",
        )
    async with session_factory() as session:
        yield session


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = get_app_settings(request)
    if not settings.secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT secret key is not configured",
        )
    try:
        payload = decode_access_token(credentials.credentials, secret_key=settings.secret_key)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = await db.get(User, payload["sub"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_task_manager(request: Request) -> Any:
    manager = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task manager is not configured",
        )
    return manager


async def get_owned_or_404(
    db: AsyncSession,
    model: type[Any],
    object_id: str,
    user_id: str,
    *,
    resource: str = "Resource",
) -> Any:
    """Fetch by both primary key and owner to prevent cross-user object access."""
    result = await db.execute(select(model).where(model.id == object_id, model.user_id == user_id))
    instance = result.scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=404, detail=f"{resource} not found")
    return instance
