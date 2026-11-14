from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=TASK_ID_PATTERN)
    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = Field(default=None, gt=0)
    approved: bool = False


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ToolResult(BaseModel):
    task_id: str
    tool: str
    ok: bool
    data: dict[str, Any] | None = None
    error: ErrorBody | None = None
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    approval_required: bool = False


@dataclass(slots=True)
class ToolOutcome:
    ok: bool = True
    data: dict[str, Any] | None = None
    error: ErrorBody | None = None
    artifacts: list[str] = field(default_factory=list)
