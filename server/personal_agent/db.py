from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from .models import Base


def make_engine(database_url: str) -> AsyncEngine:
    kwargs: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    engine = create_async_engine(database_url, **kwargs)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await connection.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                    "USING fts5(content, content='memories', content_rowid='rowid', "
                    "tokenize='trigram')"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS memories_fts_insert "
                    "AFTER INSERT ON memories BEGIN "
                    "INSERT INTO memory_fts(rowid, content) VALUES (new.rowid, new.content); END"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS memories_fts_delete "
                    "AFTER DELETE ON memories BEGIN "
                    "INSERT INTO memory_fts(memory_fts, rowid, content) "
                    "VALUES('delete', old.rowid, old.content); END"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS memories_fts_update "
                    "AFTER UPDATE ON memories BEGIN "
                    "INSERT INTO memory_fts(memory_fts, rowid, content) "
                    "VALUES('delete', old.rowid, old.content); "
                    "INSERT INTO memory_fts(rowid, content) VALUES (new.rowid, new.content); END"
                )
            )
            await connection.execute(text("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')"))


async def dispose_database(engine: AsyncEngine) -> None:
    await engine.dispose()
