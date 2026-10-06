import asyncio
import logging
import signal

from asyncpg import PostgresError
from sqlalchemy.exc import SQLAlchemyError

from dropgrid.config import Settings
from dropgrid.db.session import Database
from dropgrid.logging import configure_logging

logger = logging.getLogger(__name__)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    db = Database(settings)
    logger.info("Worker started")
    try:
        while not stop.is_set():
            try:
                await db.ping()
                logger.info("Worker heartbeat: database ok")
            except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
                logger.warning("Worker heartbeat: database unavailable")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
    finally:
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
