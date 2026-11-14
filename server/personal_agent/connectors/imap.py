import email
import email.header
import imaplib
from email.message import Message
from typing import Any

import anyio

from .base import Connector, ConnectorError


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    result: list[str] = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            result.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _decode_payload(payload: Any, charset: str) -> str:
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        return payload.decode(charset, errors="replace")
    return str(payload)


def _text_body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.get_content_disposition():
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return _decode_payload(payload, charset)
        return ""
    payload = message.get_payload(decode=True)
    return _decode_payload(payload, message.get_content_charset() or "utf-8")


class IMAPConnector(Connector):
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 993,
        mailbox: str = "INBOX",
        use_ssl: bool = True,
        timeout: float = 20,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.mailbox = mailbox
        self.use_ssl = use_ssl
        self.timeout = timeout

    def _connect(self) -> imaplib.IMAP4:
        cls = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4
        client: imaplib.IMAP4 = cls(self.host, self.port, timeout=self.timeout)
        client.login(self.username, self.password)
        client.select(self.mailbox, readonly=True)
        return client

    def _read_sync(self, limit: int, unseen_only: bool) -> dict[str, Any]:
        client = self._connect()
        try:
            criterion = "UNSEEN" if unseen_only else "ALL"
            status, data = client.search(None, criterion)
            if status != "OK":
                raise ConnectorError("IMAP 搜索失败")
            ids = data[0].split()[-limit:]
            messages: list[dict[str, Any]] = []
            for message_id in reversed(ids):
                status, payload = client.fetch(message_id, "(BODY.PEEK[] FLAGS)")
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    continue
                raw = payload[0][1]
                parsed = email.message_from_bytes(raw)
                messages.append(
                    {
                        "id": message_id.decode(errors="replace"),
                        "subject": _decode_header(parsed.get("Subject")),
                        "from": _decode_header(parsed.get("From")),
                        "date": parsed.get("Date", ""),
                        "message_id": parsed.get("Message-ID", ""),
                        "body": _text_body(parsed)[:20000],
                    }
                )
            return {"messages": messages, "count": len(messages)}
        finally:
            try:
                client.close()
            except imaplib.IMAP4.error:
                pass
            client.logout()

    async def read(self, **kwargs: Any) -> dict[str, Any]:
        limit = min(max(int(kwargs.get("limit", 20)), 1), 100)
        unseen_only = bool(kwargs.get("unseen_only", True))
        try:
            return await anyio.to_thread.run_sync(
                lambda: self._read_sync(limit, unseen_only), abandon_on_cancel=True
            )
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ConnectorError(f"IMAP 读取失败：{type(exc).__name__}") from exc

    async def test_connection(self) -> dict[str, Any]:
        def check() -> dict[str, Any]:
            client = self._connect()
            try:
                status, data = client.status(self.mailbox, "(MESSAGES UNSEEN)")
                return {"ok": status == "OK", "mailbox": self.mailbox, "raw_status": data}
            finally:
                client.logout()

        try:
            return await anyio.to_thread.run_sync(check, abandon_on_cancel=True)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ConnectorError(f"IMAP 连接失败：{type(exc).__name__}") from exc
