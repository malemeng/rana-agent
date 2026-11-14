from typing import Any

from app.models import ErrorBody


class ToolError(Exception):
    """An expected tool failure safe to return to the caller."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_body(self) -> ErrorBody:
        return ErrorBody(code=self.code, message=self.message, details=self.details)
