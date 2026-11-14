from collections.abc import AsyncIterator

import pytest
from personal_agent.db import init_database, make_engine, make_session_factory
from personal_agent.models import Agent, User
from personal_agent.security import hash_password
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    factory = make_session_factory(engine)
    async with factory() as session:
        user = User(username="owner", password_hash=hash_password("test-password"))
        session.add(user)
        await session.flush()
        session.add(Agent(user_id=user.id, name="Dot"))
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def user_id(session_factory: async_sessionmaker[AsyncSession]) -> str:
    async with session_factory() as session:
        from sqlalchemy import select

        return (await session.execute(select(User).where(User.username == "owner"))).scalar_one().id
