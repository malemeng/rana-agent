from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    token: str = Field(
        default="dev-token",
        validation_alias=AliasChoices("EXECUTOR_TOKEN", "INTERNAL_TOKEN"),
        min_length=1,
    )
    workspace_root: Path = Field(
        default=Path("./workspace"),
        validation_alias=AliasChoices("WORKSPACE_ROOT", "EXECUTOR_WORKSPACE_ROOT"),
    )
    max_file_bytes: int = Field(
        default=1024 * 1024,
        validation_alias=AliasChoices("MAX_FILE_BYTES", "EXECUTOR_MAX_FILE_BYTES"),
        gt=0,
    )
    max_list_entries: int = Field(
        default=1000,
        validation_alias=AliasChoices("MAX_LIST_ENTRIES", "EXECUTOR_MAX_LIST_ENTRIES"),
        gt=0,
    )
    shell_output_bytes: int = Field(
        default=64 * 1024,
        validation_alias=AliasChoices("SHELL_OUTPUT_BYTES", "EXECUTOR_SHELL_OUTPUT_BYTES"),
        gt=0,
    )
    default_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices(
            "DEFAULT_TIMEOUT_SECONDS", "EXECUTOR_DEFAULT_TIMEOUT_SECONDS"
        ),
        gt=0,
    )
    max_timeout_seconds: float = Field(
        default=300.0,
        validation_alias=AliasChoices("MAX_TIMEOUT_SECONDS", "EXECUTOR_MAX_TIMEOUT_SECONDS"),
        gt=0,
    )
    browser_headless: bool = Field(
        default=True,
        validation_alias=AliasChoices("BROWSER_HEADLESS", "EXECUTOR_BROWSER_HEADLESS"),
    )
    browser_max_text_chars: int = Field(
        default=200_000,
        validation_alias=AliasChoices("BROWSER_MAX_TEXT_CHARS", "EXECUTOR_BROWSER_MAX_TEXT_CHARS"),
        gt=0,
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "EXECUTOR_LOG_LEVEL"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
