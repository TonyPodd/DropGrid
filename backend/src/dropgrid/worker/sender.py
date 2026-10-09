"""Opt-in sender process; independent of the read-only publication monitor."""

import asyncio
import logging
import signal

from asyncpg import PostgresError
from sqlalchemy.exc import SQLAlchemyError

from dropgrid.config import Settings
from dropgrid.db.session import Database
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.photos import WallPhotoUploader
from dropgrid.integrations.vk.token_storage import AccountTokenCipher, DBTokenProvider
from dropgrid.logging import configure_logging
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.services.sending import CampaignSender

logger = logging.getLogger(__name__)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    db = Database(settings)
    async with VKClient(settings) as client, WallPhotoUploader(client) as uploader:
        sender = CampaignSender(
            db.sessions,
            client,
            DBTokenProvider(db.sessions, AccountTokenCipher(settings.app_secret_key)),
            LocalMediaStorage(settings.media_storage_dir),
            uploader,
        )
        try:
            while not stop.is_set():
                try:
                    await sender.tick()
                except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
                    # No exception bodies: they can contain SQL parameters/credentials.
                    logger.warning("Sender cycle interrupted; persisted phases govern recovery")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
        finally:
            await db.close()


async def main() -> None:
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run(Settings(), stop)


if __name__ == "__main__":
    asyncio.run(main())
