from abc import ABC, abstractmethod
from typing import Any


class ConnectorError(RuntimeError):
    """A connector failed with a sanitized, user-presentable message."""


class Connector(ABC):
    @abstractmethod
    async def read(self, **kwargs: Any) -> dict[str, Any]:
        """Read source data without changing remote state."""

    @abstractmethod
    async def test_connection(self) -> dict[str, Any]:
        """Check connectivity and return non-secret source metadata."""
