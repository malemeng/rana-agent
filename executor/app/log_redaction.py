import logging
import re
from collections.abc import Iterable

_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?([^\s,;]+)")
_SENSITIVE_RE = re.compile(
    r"(?i)([\"\']?(?:token|password|passwd|secret|api[_-]?key|cookie)[\"\']?"
    r"\s*[:=]\s*)([\"\']?)([^\"\'\s,;&}]+)([\"\']?)"
)


class RedactingFilter(logging.Filter):
    """Redact common credential formats and configured secrets from log lines."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = tuple(secret for secret in secrets if secret)

    def _redact(self, message: str) -> str:
        message = _BEARER_RE.sub(r"\1<redacted>", message)
        message = _SENSITIVE_RE.sub(r"\1<redacted>", message)
        for secret in self.secrets:
            message = message.replace(secret, "<redacted>")
        return message

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            # Uvicorn's AccessFormatter requires the original five-element args
            # shape and formats the request line itself instead of record.msg.
            record.args = tuple(
                self._redact(item) if isinstance(item, str) else item for item in record.args
            )
            record.msg = self._redact(str(record.msg))
            return True
        record.msg = self._redact(record.getMessage())
        record.args = ()
        return True


def install_log_redaction(secret: str, level: str = "INFO") -> None:
    """Install redaction on application and pre-existing uvicorn handlers."""

    def add_or_update(target: logging.Logger | logging.Handler) -> None:
        for item in target.filters:
            if isinstance(item, RedactingFilter):
                if secret and secret not in item.secrets:
                    item.secrets = (*item.secrets, secret)
                break
        else:
            target.addFilter(RedactingFilter((secret,)))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    loggers = [
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("executor"),
    ]
    for logger in loggers:
        add_or_update(logger)
        for handler in logger.handlers:
            add_or_update(handler)
