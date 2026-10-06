import asyncio
import logging

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from asyncpg import PostgresError
from sqlalchemy.exc import SQLAlchemyError

from dropgrid.config import Settings
from dropgrid.db.session import Database
from dropgrid.logging import configure_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    settings = Settings()
    token = settings.telegram_bot_token
    if token is None or not token.get_secret_value().strip():
        raise SystemExit("TELEGRAM_BOT_TOKEN is required to start the bot")
    db = Database(settings)
    router = Router()

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer("DropGrid foundation. Use /help or /status.")

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "/start — welcome\n/help — commands\n/status — backend and database health"
        )

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        backend_ok = False
        database_ok = False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(settings.backend_url.rstrip("/") + "/health")
                backend_ok = response.status_code == 200
        except httpx.HTTPError:
            logger.warning("Bot backend health check failed")
        try:
            await db.ping()
            database_ok = True
        except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
            logger.warning("Bot database health check failed")
        await message.answer(
            f"Backend: {'ok' if backend_ok else 'unavailable'}\n"
            f"Database: {'ok' if database_ok else 'unavailable'}"
        )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        async with Bot(token=token.get_secret_value()) as bot:
            await dispatcher.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        # Third-party errors may include token-bearing URLs: don't print exception text.
        logger.error("Bot stopped due to an error; verify token and connectivity")
        raise SystemExit(1) from None
