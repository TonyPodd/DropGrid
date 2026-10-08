import asyncio
import logging

import pytest

from dropgrid.bot.__main__ import main as bot_main
from dropgrid.config import Settings
from dropgrid.logging import JsonFormatter
from dropgrid.worker.__main__ import run


def test_settings_hide_secrets() -> None:
    settings = Settings(
        _env_file=None, database_url="sentinel-secret", telegram_bot_token="fake-secret"
    )
    assert "sentinel-secret" not in repr(settings)
    assert "fake-secret" not in repr(settings)


def test_test_settings_ignore_local_dotenv(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("PIXABAY_API_KEY=dotenv-sentinel\nVK_WRITE_ENABLED=true\n")
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)
    assert settings.pixabay_api_key is None
    assert settings.vk_write_enabled is False
    # Runtime behaviour remains intact: only test constructors opt out.
    assert Settings().pixabay_api_key.get_secret_value() == "dotenv-sentinel"


def test_json_logging() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, "heartbeat", (), None)
    formatted = JsonFormatter().format(record)
    assert all(key in formatted for key in ("timestamp", "level", "logger", "message"))


async def test_bot_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        await bot_main()


async def test_worker_stops_when_database_unavailable() -> None:
    stop = asyncio.Event()
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        worker_poll_seconds=0.01,
    )
    task = asyncio.create_task(run(settings, stop))
    await asyncio.sleep(0.03)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
