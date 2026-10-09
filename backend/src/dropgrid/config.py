from pathlib import Path
from typing import Annotated
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret_key: SecretStr = SecretStr("")  # Fernet key for local Account credential encryption.
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://dropgrid:dropgrid@localhost:5432/dropgrid"
    )
    telegram_bot_token: SecretStr | None = None
    backend_host: str = "0.0.0.0"
    backend_port: int = Field(default=8000, ge=1, le=65535)
    backend_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:5173"
    worker_poll_seconds: float = Field(default=10, gt=0)

    pixabay_api_key: SecretStr | None = None
    media_storage_dir: Path = Path("media")
    photo_max_reuse_per_asset: int = Field(default=3, ge=1, le=20)
    photo_search_cache_hours: int = Field(default=24, ge=24, le=168)
    photo_download_concurrency: int = Field(default=4, ge=1, le=6)
    campaign_reference_warmup_target: int = Field(default=12, ge=1, le=20)
    campaign_reference_recent_days: int = Field(default=180, ge=1, le=365)
    media_preparation_concurrency: int = Field(default=2, ge=1, le=4)
    vk_read_concurrency: int = Field(default=1, ge=1, le=3)
    photo_download_timeout_seconds: float = Field(default=10, gt=0, le=25)
    photo_download_spare: int = Field(default=3, ge=0, le=5)
    visual_embedding_enabled: bool = False
    visual_model_path: Path = Path("models/clip-vision-int8.onnx")

    @field_validator("media_storage_dir", mode="before")
    @classmethod
    def storage_directory(cls, value: object) -> object:
        return Path("media") if value == "" else value

    vk_api_version: str = "5.199"
    vk_write_enabled: bool = False
    vk_send_interval_seconds: float = Field(default=60, ge=1, le=86400)
    vk_test_allowed_community_ids: Annotated[frozenset[int], NoDecode] = frozenset()
    vk_test_access_token: SecretStr | None = None
    vk_test_account_id: UUID | None = None
    vk_timeout_seconds: float = Field(default=10, gt=0, le=60)
    # Multipart response may take longer than ordinary API reads; still bounded, no retry.
    vk_upload_timeout_seconds: float = Field(default=30, gt=0, le=120)
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
