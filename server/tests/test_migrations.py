import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_alembic_upgrade_and_model_check(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "migration.db"
    env = os.environ.copy()
    env["AGENT_DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    env["AGENT_SECRET_KEY"] = "migration-test-key-with-at-least-32-bytes"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
        assert {"users", "tasks", "memories", "memory_fts", "alembic_version"} <= tables
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
