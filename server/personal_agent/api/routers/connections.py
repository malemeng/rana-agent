"""External connection CRUD with recursive credential redaction."""

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Connection, User
from ...schemas import ConnectionCreate, ConnectionOut, ConnectionUpdate
from ...security import mask_secret_data, restore_masked_data
from ..deps import get_current_user, get_db, get_owned_or_404

router = APIRouter(prefix="/connections", tags=["connections"])


def _redacted(connection: Connection) -> ConnectionOut:
    return ConnectionOut.model_validate(connection).model_copy(
        update={"config": mask_secret_data(connection.config_json)}
    )


@router.get("", response_model=list[ConnectionOut])
async def list_connections(
    kind: str | None = None,
    enabled: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConnectionOut]:
    statement = select(Connection).where(Connection.user_id == current_user.id)
    if kind is not None:
        statement = statement.where(Connection.kind == kind)
    if enabled is not None:
        statement = statement.where(Connection.enabled == enabled)
    result = await db.execute(
        statement.order_by(Connection.created_at.desc()).limit(limit).offset(offset)
    )
    return [_redacted(connection) for connection in result.scalars().all()]


@router.post("", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: ConnectionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectionOut:
    values = payload.model_dump(exclude={"config"})
    connection = Connection(
        user_id=current_user.id,
        config_json=payload.config,
        **values,
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return _redacted(connection)


@router.get("/{connection_id}", response_model=ConnectionOut)
async def get_connection(
    connection_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectionOut:
    connection = await get_owned_or_404(
        db, Connection, connection_id, current_user.id, resource="Connection"
    )
    return _redacted(connection)


@router.patch("/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: str,
    payload: ConnectionUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectionOut:
    connection = await get_owned_or_404(
        db, Connection, connection_id, current_user.id, resource="Connection"
    )
    changes = payload.model_dump(exclude_unset=True, exclude={"config"})
    for key, value in changes.items():
        setattr(connection, key, value)
    if payload.config is not None:
        connection.config_json = restore_masked_data(payload.config, connection.config_json)
    await db.commit()
    await db.refresh(connection)
    return _redacted(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    connection = await get_owned_or_404(
        db, Connection, connection_id, current_user.id, resource="Connection"
    )
    await db.delete(connection)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
