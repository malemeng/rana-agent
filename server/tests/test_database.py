from personal_agent.db import init_database, make_engine, make_session_factory
from personal_agent.models import Memory, User
from personal_agent.security import hash_password
from sqlalchemy import text


async def test_sqlite_memory_fts_tracks_insert_update_delete() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    factory = make_session_factory(engine)
    async with factory() as session:
        user = User(username="fts", password_hash=hash_password("password"))
        session.add(user)
        await session.flush()
        memory = Memory(user_id=user.id, content="喜欢在周一早上查看项目摘要")
        session.add(memory)
        await session.commit()
        rows = (
            await session.execute(
                text(
                    "SELECT memories.id FROM memories JOIN memory_fts "
                    "ON memories.rowid = memory_fts.rowid WHERE memory_fts MATCH :query"
                ),
                {"query": "项目摘要"},
            )
        ).all()
        assert len(rows) == 1
        memory.content = "喜欢在周五晚上看电影"
        await session.commit()
        assert (
            await session.execute(
                text("SELECT count(*) FROM memory_fts WHERE memory_fts MATCH '项目摘要'")
            )
        ).scalar_one() == 0
        await session.delete(memory)
        await session.commit()
        assert (await session.execute(text("SELECT count(*) FROM memory_fts"))).scalar_one() == 0
    await engine.dispose()


async def test_user_username_unique() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    factory = make_session_factory(engine)
    async with factory() as session:
        session.add(User(username="same", password_hash="x"))
        await session.commit()
        session.add(User(username="same", password_hash="x"))
        try:
            await session.commit()
        except Exception as exc:
            assert "UNIQUE" in str(exc)
        else:
            raise AssertionError("unique constraint not enforced")
    await engine.dispose()
