from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_", env_file=".env", extra="ignore", case_sensitive=False
    )

    app_name: str = "Personal Agent"
    environment: str = "development"
    data_dir: Path = Path("./data")
    database_url: str | None = None
    secret_key: str | None = None
    access_token_minutes: int = 30
    refresh_token_days: int = 30

    admin_username: str = "admin"
    admin_password: str | None = None

    ai_provider: Literal["mimo", "minimax"] = "mimo"
    llm_provider: Literal["mimo", "minimax"] | None = None
    asr_provider: Literal["mimo", "minimax"] | None = None
    tts_provider: Literal["mimo", "minimax"] | None = None
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_api_key: str | None = None
    mimo_llm_model: str = "mimo-v2.6-flash"
    mimo_asr_model: str = "mimo-v2.5-asr"
    mimo_tts_model: str = "mimo-v2.5-tts"
    mimo_voice: str = "mimo_default"

    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_region: Literal["global", "cn"] = "global"
    minimax_api_key: str | None = None
    minimax_llm_model: str = "MiniMax-M3.1-Flash"
    minimax_asr_model: str = "asr-1.0"
    minimax_tts_model: str = "speech-2.8-turbo"
    minimax_voice: str = "Chinese (Mandarin)_Lyrical_Voice"
    minimax_reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    request_timeout_seconds: float = 90.0
    max_retries: int = 3

    max_async_tasks: int = Field(default=4, ge=1, le=64)
    max_thread_workers: int = Field(default=2, ge=1, le=32)
    max_browser_sessions: int = Field(default=1, ge=1, le=8)
    dispatch_interval_ms: int = Field(default=200, ge=50, le=5000)
    task_heartbeat_seconds: int = 10
    task_timeout_seconds: int = Field(default=600, ge=5, le=86400)
    agent_max_steps: int = Field(default=8, ge=1, le=50)
    scheduler_interval_seconds: float = 1.0

    executor_base_url: str = "http://executor:8081"
    executor_token: str = "development-executor-token"
    executor_timeout_seconds: float = 120.0

    cors_origins: str = "*"
    log_level: str = "INFO"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'agent.db').resolve()}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
