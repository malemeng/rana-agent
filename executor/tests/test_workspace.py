import os
import stat

import pytest

from .conftest import execute


def test_workspace_write_read_list_delete_and_download(client):
    written = execute(
        client,
        "workspace_write",
        {"path": "notes/a.txt", "content": "你好，Executor"},
    )
    assert written.status_code == 200
    assert written.json()["ok"] is True
    assert written.json()["artifacts"] == ["notes/a.txt"]
    assert written.json()["data"]["encoding"] == "utf-8"

    read = execute(client, "workspace_read", {"path": "notes/a.txt"})
    assert read.json()["data"]["content"] == "你好，Executor"

    listed = execute(client, "workspace_list", {"path": ".", "recursive": True})
    entries = listed.json()["data"]["entries"]
    assert {entry["path"] for entry in entries} == {"notes", "notes/a.txt"}

    downloaded = client.get(
        "/api/v1/artifacts/task-1/notes/a.txt",
        headers={"Authorization": "Bearer test-token"},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == "你好，Executor".encode()
    assert "attachment" in downloaded.headers["content-disposition"]

    deleted = execute(client, "workspace_delete", {"path": "notes/a.txt"})
    assert deleted.json()["data"]["deleted"] is True
    assert (
        execute(client, "workspace_read", {"path": "notes/a.txt"}).json()["error"]["code"]
        == "FILE_NOT_FOUND"
    )
    assert (
        client.get(
            "/api/v1/artifacts/task-1/notes/a.txt",
            headers={"Authorization": "Bearer test-token"},
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "../../etc/passwd",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "..\\outside.txt",
        "safe/../../outside.txt",
    ],
)
def test_path_traversal_is_rejected(client, tmp_path, path):
    response = execute(client, "workspace_write", {"path": path, "content": "not-written"})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] in {
        "PATH_TRAVERSAL",
        "INVALID_PATH",
    }
    assert not (tmp_path / "outside.txt").exists()


def test_task_directories_are_isolated(client):
    for task_id, content in (("task-a", "A"), ("task-b", "B")):
        response = execute(
            client,
            "workspace_write",
            {"path": "same.txt", "content": content},
            task_id=task_id,
        )
        assert response.json()["ok"] is True
    assert (
        execute(client, "workspace_read", {"path": "same.txt"}, task_id="task-a").json()["data"][
            "content"
        ]
        == "A"
    )
    assert (
        execute(client, "workspace_read", {"path": "same.txt"}, task_id="task-b").json()["data"][
            "content"
        ]
        == "B"
    )


def test_file_size_limit_and_atomic_temp_cleanup(client, settings):
    too_large = execute(client, "workspace_write", {"path": "large.txt", "content": "x" * 129})
    assert too_large.json()["error"]["code"] == "FILE_TOO_LARGE"

    task_root = settings.workspace_root / "task-1"
    task_root.mkdir(parents=True, exist_ok=True)
    (task_root / "large.txt").write_bytes(b"x" * 129)
    read_large = execute(client, "workspace_read", {"path": "large.txt"})
    assert read_large.json()["error"]["code"] == "FILE_TOO_LARGE"

    assert (
        execute(client, "workspace_write", {"path": "ok.txt", "content": "ok"}).json()["ok"] is True
    )
    assert list(task_root.glob("*.tmp")) == []
    assert not any(path.name.endswith(".tmp") for path in task_root.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_task_directory_and_file_permissions(client, settings):
    assert (
        execute(client, "workspace_write", {"path": "private.txt", "content": "secret"}).json()[
            "ok"
        ]
        is True
    )
    task_root = settings.workspace_root / "task-1"
    assert stat.S_IMODE(task_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((task_root / "private.txt").stat().st_mode) == 0o600


def test_artifact_path_traversal_and_missing_file(client):
    traversal = client.get(
        "/api/v1/artifacts/task-1/..%2Foutside.txt",
        headers={"Authorization": "Bearer test-token"},
    )
    assert traversal.status_code == 400
    missing = client.get(
        "/api/v1/artifacts/task-1/missing.txt",
        headers={"Authorization": "Bearer test-token"},
    )
    assert missing.status_code == 404
