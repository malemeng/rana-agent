import logging
import secrets

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .models import Agent, User
from .security import hash_password

logger = logging.getLogger("personal_agent.bootstrap")


def ensure_persistent_secret(settings: Settings) -> str:
    if settings.secret_key:
        return settings.secret_key
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    key_path = settings.data_dir / ".secret_key"
    if key_path.exists():
        settings.secret_key = key_path.read_text(encoding="utf-8").strip()
        return settings.secret_key
    value = secrets.token_urlsafe(48)
    try:
        key_path.write_text(value, encoding="utf-8")
        key_path.chmod(0o600)
    except OSError:
        logger.exception("持久密钥写入失败")
        raise
    settings.secret_key = value
    return value


async def bootstrap_identity(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with session_factory() as session:
        user_count = await session.scalar(select(func.count(User.id)))
        if not user_count:
            generated_password = settings.admin_password is None
            password = settings.admin_password or secrets.token_urlsafe(14)
            user = User(username=settings.admin_username, password_hash=hash_password(password))
            session.add(user)
            await session.flush()
            session.add(
                Agent(
                    user_id=user.id,
                    name="Rana",
                    system_prompt=(
                        "你是用户持续在线的个人 Agent。保持项目上下文，主动拆解目标，"
                        "清楚报告进展；外部写操作先生成审批。"
                    ),
                    model=settings.mimo_llm_model,
                    voice=settings.mimo_voice,
                )
            )
            await session.commit()
            if generated_password:
                logger.warning(
                    "首次启动已创建管理员，用户名：%s，随机密码：%s；登录后请更新配置",
                    settings.admin_username,
                    password,
                )
            return

        users = (await session.execute(select(User))).scalars().all()
        renamed = await session.execute(
            update(Agent).where(Agent.name == "Dot").values(name="Rana")
        )
        added = bool(getattr(renamed, "rowcount", 0))
        for user in users:
            agent_count = await session.scalar(
                select(func.count(Agent.id)).where(Agent.user_id == user.id)
            )
            if not agent_count:
                session.add(
                    Agent(
                        user_id=user.id,
                        name="Rana",
                        model=settings.mimo_llm_model,
                        voice=settings.mimo_voice,
                    )
                )
                added = True
        if added:
            await session.commit()
