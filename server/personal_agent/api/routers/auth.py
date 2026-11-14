"""Login, token rotation, logout, and current-user endpoints."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...models import AuthSession, User
from ...schemas import LoginRequest, RefreshRequest, TokenResponse, UserOut
from ...security import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    verify_password,
)
from ..deps import get_app_settings, get_current_user, get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _new_auth_session(user_id: str, refresh_token: str, settings: Settings) -> AuthSession:
    return AuthSession(
        user_id=user_id,
        refresh_hash=hash_refresh_token(refresh_token),
        expires_at=_utcnow() + timedelta(days=settings.refresh_token_days),
    )


def _token_response(user: User, refresh_token: str, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            user.id,
            secret_key=settings.secret_key or "",
            expires_minutes=settings.access_token_minutes,
        ),
        refresh_token=refresh_token,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request_settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not request_settings.secret_key:
        raise HTTPException(status_code=503, detail="JWT secret key is not configured")
    refresh_token = create_refresh_token()
    db.add(_new_auth_session(user.id, refresh_token, request_settings))
    await db.commit()
    return _token_response(user, refresh_token, request_settings)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request_settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    if not request_settings.secret_key:
        raise HTTPException(status_code=503, detail="JWT secret key is not configured")
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.refresh_hash == hash_refresh_token(payload.refresh_token)
        )
    )
    auth_session = result.scalar_one_or_none()
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= _utcnow()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or revoked refresh token",
        )
    user = await db.get(User, auth_session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")

    # A conditional write makes rotation single-use even when two requests
    # concurrently validated the same old token.
    revoked_at = _utcnow()
    revoked = await db.execute(
        update(AuthSession)
        .where(AuthSession.id == auth_session.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=revoked_at)
    )
    if getattr(revoked, "rowcount", 0) != 1:
        await db.rollback()
        raise HTTPException(status_code=401, detail="Refresh token was already rotated")
    rotated_refresh_token = create_refresh_token()
    db.add(_new_auth_session(user.id, rotated_refresh_token, request_settings))
    await db.commit()
    return _token_response(user, rotated_refresh_token, request_settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        select(AuthSession).where(
            AuthSession.refresh_hash == hash_refresh_token(payload.refresh_token),
            AuthSession.user_id == current_user.id,
        )
    )
    auth_session = result.scalar_one_or_none()
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = _utcnow()
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
