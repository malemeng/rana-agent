"""Per-user settings API. Secret values are write-only through REST responses."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Setting, User
from ...schemas import SettingCreate, SettingOut, SettingWrite
from ...security import MASKED_SECRET, is_sensitive_key, mask_secret_data
from ..deps import get_current_user, get_db

router = APIRouter(prefix="/settings", tags=["settings"])


def _redacted(setting: Setting) -> SettingOut:
    should_mask = setting.is_secret or is_sensitive_key(setting.key)
    return SettingOut.model_validate(setting).model_copy(
        update={
            "value": mask_secret_data(setting.value_json, force=should_mask),
            "is_secret": should_mask,
        }
    )


@router.get("", response_model=list[SettingOut])
async def list_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SettingOut]:
    result = await db.execute(
        select(Setting).where(Setting.user_id == current_user.id).order_by(Setting.key)
    )
    return [_redacted(setting) for setting in result.scalars().all()]


@router.post("", response_model=SettingOut, status_code=status.HTTP_201_CREATED)
async def create_setting(
    payload: SettingCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettingOut:
    result = await db.execute(
        select(Setting).where(
            Setting.user_id == current_user.id,
            Setting.key == payload.key,
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Setting already exists")
    setting = Setting(
        user_id=current_user.id,
        key=payload.key,
        value_json=payload.value,
        is_secret=payload.is_secret or is_sensitive_key(payload.key),
    )
    db.add(setting)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Setting already exists") from exc
    await db.refresh(setting)
    return _redacted(setting)


@router.get("/test-connection")
async def test_connection(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict[str, bool | str]:
    provider = getattr(request.app.state, "ai_provider", None) or getattr(
        request.app.state, "mimo_provider", None
    )
    provider_name = getattr(provider, "provider_name", "mimo") if provider else "mimo"
    if provider_name == "hybrid":
        label = getattr(provider, "display_name", "混合 Provider")
    else:
        label = "MiniMax" if provider_name == "minimax" else "MiMo"
    if provider is None:
        return {"success": False, "message": "模型 Provider 尚未初始化"}
    missing = getattr(provider, "missing_credentials", [])
    if missing:
        return {
            "success": False,
            "message": f"请在 NAS 的 .env 配置：{', '.join(missing)}",
        }
    if not provider.api_key:
        key_name = "AGENT_MINIMAX_API_KEY" if provider_name == "minimax" else "AGENT_MIMO_API_KEY"
        return {"success": False, "message": f"请在 NAS 的 .env 配置 {key_name}"}
    try:
        result = await provider.complete([{"role": "user", "content": "连接测试，只回复：ok"}])
    except Exception as exc:
        return {"success": False, "message": f"{label} 连接失败：{type(exc).__name__}"}
    return {
        "success": True,
        "provider": provider_name,
        "message": f"{label} 连接正常（{provider.model}）",
        "response": str(getattr(result, "content", ""))[:100],
    }


@router.get("/{key}", response_model=SettingOut)
async def get_setting(
    key: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettingOut:
    result = await db.execute(
        select(Setting).where(Setting.user_id == current_user.id, Setting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    return _redacted(setting)


@router.put("/{key}", response_model=SettingOut)
async def upsert_setting(
    key: str,
    payload: SettingWrite,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettingOut:
    result = await db.execute(
        select(Setting).where(Setting.user_id == current_user.id, Setting.key == key)
    )
    setting = result.scalar_one_or_none()
    value = payload.value
    if setting is not None and value == MASKED_SECRET:
        value = setting.value_json
    if setting is None:
        setting = Setting(
            user_id=current_user.id,
            key=key,
            value_json=value,
            is_secret=bool(payload.is_secret) or is_sensitive_key(key),
        )
        db.add(setting)
    else:
        setting.value_json = value
        if payload.is_secret is not None:
            setting.is_secret = payload.is_secret
        setting.is_secret = setting.is_secret or is_sensitive_key(key)
    await db.commit()
    await db.refresh(setting)
    return _redacted(setting)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_setting(
    key: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        select(Setting).where(Setting.user_id == current_user.id, Setting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    await db.delete(setting)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
