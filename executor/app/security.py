import hmac
from typing import Annotated

from app.config import Settings
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    settings: Settings = request.app.state.settings
    supplied = credentials.credentials if credentials is not None else None
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or supplied is None
        or not hmac.compare_digest(supplied, settings.token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "缺少或无效的 Bearer token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
