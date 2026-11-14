import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

from personal_agent.config import get_settings  # noqa: E402
from personal_agent.models import Base  # noqa: E402

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.db_url.replace("+aiosqlite", ""))
target_metadata = Base.metadata


def include_table(name: str, type_: str, reflected: bool, compare_to: object) -> bool:
    """FTS5 shadow tables are managed by explicit SQL in migrations."""
    return not name.startswith("memory_fts")


def include_object(obj: object, name: str, type_: str, reflected: bool, compare_to: object) -> bool:
    if type_ == "table" and name.startswith("memory_fts"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        include_table=include_table,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if url.startswith("sqlite"):
        database_url = url.removeprefix("sqlite:///")
        if database_url != ":memory:":
            Path(database_url).parent.mkdir(parents=True, exist_ok=True)
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            include_table=include_table,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
