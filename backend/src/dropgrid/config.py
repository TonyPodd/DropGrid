from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
