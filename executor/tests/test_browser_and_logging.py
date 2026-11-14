import logging

from app.errors import ToolError
from app.log_redaction import RedactingFilter

from .conftest import execute


def test_browser_unavailable_is_structured(client, monkeypatch):
    async def unavailable():
        raise ToolError(
            "BROWSER_UNAVAILABLE",
            "Chromium 浏览器未安装或启动失败",
            {"reason": "test_fixture"},
        )

    monkeypatch.setattr(client.app.state.service.browser, "_ensure_browser", unavailable)
    response = execute(
        client,
        "browser",
        {"action": "open", "url": "https://example.test"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"] == {
        "code": "BROWSER_UNAVAILABLE",
        "message": "Chromium 浏览器未安装或启动失败",
        "details": {"reason": "test_fixture"},
    }


def test_log_filter_redacts_tokens_and_credentials():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=("Authorization: Bearer abc.def.ghi token=test-token password=hunter2 plain=value"),
        args=(),
        exc_info=None,
    )
    RedactingFilter(("test-token",)).filter(record)
    message = record.getMessage()
    assert "abc.def.ghi" not in message
    assert "test-token" not in message
    assert "hunter2" not in message
    assert "plain=value" in message


def test_uvicorn_access_log_keeps_structured_args_and_redacts_query():
    from uvicorn.logging import AccessFormatter

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s %s" %d',
        args=(
            "127.0.0.1:12345",
            "GET",
            "/ws/voice?token=test-token",
            "HTTP/1.1",
            101,
        ),
        exc_info=None,
    )
    RedactingFilter(("test-token",)).filter(record)
    message = AccessFormatter().format(record)
    assert "test-token" not in message
    assert "101" in message
    assert len(record.args) == 5
