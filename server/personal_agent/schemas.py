"""Pydantic request and response contracts for the REST API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: str
    username: str
    is_admin: bool
    created_at: datetime


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=200)
    summary: str = ""
    agent_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = None
    agent_id: str | None = None


class ConversationOut(ORMModel):
    id: str
    title: str
    summary: str
    agent_id: str | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class MessageOut(ORMModel):
    id: str
    conversation_id: str
    role: str
    content: str
    status: str
    turn_id: str | None
    created_at: datetime


class ConversationTaskResponse(BaseModel):
    message: MessageOut
    task: "TaskOut"


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    input: str = Field(min_length=1, max_length=200_000)
    priority: int = Field(default=50, ge=0, le=100)
    idempotency_key: str | None = Field(default=None, max_length=120)


class GoalUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    input: str | None = Field(default=None, min_length=1, max_length=200_000)
    priority: int | None = Field(default=None, ge=0, le=100)
    status: Literal["active", "paused", "completed", "cancelled"] | None = None


class GoalStatusUpdate(BaseModel):
    status: Literal["active", "paused", "completed", "cancelled"]


class GoalOut(ORMModel):
    id: str
    title: str
    input: str
    status: str
    priority: int
    idempotency_key: str | None
    result: str
    error: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GoalTaskResponse(BaseModel):
    goal: GoalOut
    task: "TaskOut"


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    priority: int | None = Field(default=None, ge=0, le=100)
    progress: float | None = Field(default=None, ge=0, le=100)
    max_attempts: int | None = Field(default=None, ge=1, le=100)
    timeout_seconds: int | None = Field(default=None, ge=5, le=86_400)
    status: Literal["queued", "paused", "succeeded", "failed", "cancelled"] | None = None


class TaskStatusUpdate(BaseModel):
    status: Literal["queued", "paused", "succeeded", "failed", "cancelled"]


class TaskOut(ORMModel):
    id: str
    goal_id: str | None
    parent_id: str | None
    title: str
    kind: str
    input_json: dict[str, Any]
    status: str
    priority: int
    progress: float
    attempts: int
    max_attempts: int
    timeout_seconds: int
    idempotency_key: str | None
    idempotent: bool
    lease_until: datetime | None
    heartbeat_at: datetime | None
    result_json: dict[str, Any]
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    kind: str = Field(default="candidate", min_length=1, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=50)
    pinned: bool = False
    archived: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_message_id: str | None = None


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=200_000)
    kind: str | None = Field(default=None, min_length=1, max_length=30)
    tags: list[str] | None = Field(default=None, max_length=50)
    pinned: bool | None = None
    archived: bool | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_message_id: str | None = None


class MemoryOut(ORMModel):
    id: str
    kind: str
    content: str
    tags: list[str]
    pinned: bool
    archived: bool
    confidence: float
    source_message_id: str | None
    created_at: datetime
    updated_at: datetime


class ScheduleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=200_000)
    cron: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    enabled: bool = True
    next_run_at: datetime | None = None


class ScheduleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = Field(default=None, min_length=1, max_length=200_000)
    cron: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    next_run_at: datetime | None = None


class ScheduleOut(ORMModel):
    id: str
    title: str
    prompt: str
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConnectionCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ConnectionUpdate(BaseModel):
    kind: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class ConnectionOut(ORMModel):
    id: str
    kind: str
    name: str
    config: dict[str, Any] = Field(validation_alias="config_json")
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ApprovalDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=10_000)


class ApprovalOut(ORMModel):
    id: str
    task_id: str
    action: str
    payload_json: dict[str, Any]
    status: str
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalTaskResponse(BaseModel):
    approval: ApprovalOut
    task: TaskOut


class SettingCreate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: Any = None
    is_secret: bool = False


class SettingWrite(BaseModel):
    value: Any = None
    is_secret: bool | None = None


class SettingOut(ORMModel):
    id: str
    key: str
    value: Any = Field(validation_alias="value_json")
    is_secret: bool
    created_at: datetime
    updated_at: datetime


class ActivityOut(ORMModel):
    id: int
    goal_id: str | None
    task_id: str | None
    type: str
    payload_json: dict[str, Any]
    created_at: datetime


class ArtifactOut(ORMModel):
    id: str
    task_id: str
    name: str
    path: str
    content_type: str
    size: int
    created_at: datetime


ConversationTaskResponse.model_rebuild()
GoalTaskResponse.model_rebuild()
