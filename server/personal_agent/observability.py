import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

SENSITIVE_HEADERS = {"authorization", "api-key", "cookie", "x-internal-token"}


class RedactSensitiveFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "headers"):
            record.headers = {
                key: ("[redacted]" if key.lower() in SENSITIVE_HEADERS else value)
                for key, value in record.headers.items()
            }
        return True


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logging.getLogger("personal_agent.http").info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )
        return response


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
