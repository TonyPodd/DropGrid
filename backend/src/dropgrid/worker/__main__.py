import asyncio
import logging
import signal

from asyncpg import PostgresError
from sqlalchemy.exc import SQLAlchemyError

from dropgrid.config import Settings
from dropgrid.db.session import Database
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.token_storage import AccountTokenCipher, DBTokenProvider
from dropgrid.logging import configure_logging
from dropgrid.services.publication import PublicationMonitor
from dropgrid.services.sending import refresh_campaigns

logger = logging.getLogger(__name__)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    db = Database(settings)
    client = VKClient(settings)
    monitor = PublicationMonitor(
        db.sessions,
        client,
        DBTokenProvider(db.sessions, AccountTokenCipher(settings.app_secret_key)),
    )
    logger.info("Worker started")
    try:
        while not stop.is_set():
            try:
                await db.ping()
                await monitor.tick()
                await refresh_campaigns(db.sessions)
                logger.info("Worker publication monitor: cycle complete")
            except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
                logger.warning("Worker heartbeat: database unavailable")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await client.aclose()
        await db.close()
        logger.info("Worker stopped")


async def main() -> None:
    configure_logging()
    settings = Settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run(settings, stop)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


if __name__ == "__main__":
    asyncio.run(main())
