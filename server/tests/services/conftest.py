from __future__ import annotations

import pytest
from personal_agent.db import make_engine, make_session_factory
from personal_agent.models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.fixture
async def session_factory(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'runtime.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = make_session_factory(engine)
    assert isinstance(factory, async_sessionmaker)
    yield factory
    await engine.dispose()
