from typing import Annotated
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret_key: SecretStr = SecretStr("")  # Reserved; no authentication in this phase.
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://dropgrid:dropgrid@localhost:5432/dropgrid"
    )
    telegram_bot_token: SecretStr | None = None
    backend_host: str = "0.0.0.0"
    backend_port: int = Field(default=8000, ge=1, le=65535)
    backend_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:5173"
    worker_poll_seconds: float = Field(default=10, gt=0)

    vk_api_version: str = "5.199"
    vk_write_enabled: bool = False
    vk_test_allowed_community_ids: Annotated[frozenset[int], NoDecode] = frozenset()
    vk_test_access_token: SecretStr | None = None
    vk_test_account_id: UUID | None = None
    vk_timeout_seconds: float = Field(default=10, gt=0, le=60)
    vk_max_attempts: int = Field(default=3, ge=1, le=5)
    vk_min_interval_seconds: float = Field(default=1, ge=0.34)
    vk_max_photo_bytes: int = Field(default=10 * 1024 * 1024, gt=0, le=50 * 1024 * 1024)

    @field_validator("vk_api_version")
    @classmethod
    def api_version(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"5\.\d{1,3}", value):
            raise ValueError("Expected a VK API 5.x version")
        return value

    @field_validator("vk_test_allowed_community_ids", mode="before")
    @classmethod
    def community_allowlist(cls, value: object) -> frozenset[int]:
        if isinstance(value, str):
            try:
                ids = frozenset(int(item.strip()) for item in value.split(",") if item.strip())
            except ValueError:
                raise ValueError(
                    "Expected comma-separated positive community IDs, no wildcard"
                ) from None
        elif isinstance(value, (set, frozenset, list, tuple)):
            ids = frozenset(value)
        else:
            raise ValueError("Expected community IDs")
        if any(type(item) is not int or not 0 < item <= 2**63 - 1 for item in ids):
            raise ValueError("Community IDs must be positive 64-bit integers")
        return ids

    @field_validator("vk_test_account_id", mode="before")
    @classmethod
    def optional_account_id(cls, value: object) -> object:
        return None if value == "" else value
