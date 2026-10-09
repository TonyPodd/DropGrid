"""Explicit reference-study worker. Does not instantiate sender or monitor."""

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
from dropgrid.photos.engine import PhotoEngine
from dropgrid.photos.reference_jobs import ReferenceJobs
from dropgrid.photos.references import CommunityReferenceCollector

logger = logging.getLogger(__name__)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    settings = settings.model_copy(
        update={"vk_write_enabled": False, "vk_test_allowed_community_ids": ()}
    )
    db = Database(settings)
    client = VKClient(settings)
    engine = PhotoEngine(settings, db)
    service = ReferenceJobs(
        CommunityReferenceCollector(
            db.sessions,
            client,
            DBTokenProvider(db.sessions, AccountTokenCipher(settings.app_secret_key)),
            engine.reference_downloader,
            engine.reference_storage,
            engine.embedder,
        )
    )
    try:
        while not stop.is_set():
            try:
                processed = await service.tick()
            except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
                logger.warning("Reference study worker temporarily unavailable")
                processed = 0
            if processed:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await engine.aclose()
        await client.aclose()
        await db.close()


async def main() -> None:
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run(Settings(), stop)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


if __name__ == "__main__":
    asyncio.run(main())
