"""Lifespan-owned pools and provider-neutral composition."""

import httpx

from dropgrid.config import Settings
from dropgrid.db.session import Database
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import PhotoPolicy
from dropgrid.photos.download import PhotoDownloader, PinnedPhotoTransport
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.pixabay import PixabayPhotoProvider
from dropgrid.photos.planner import CampaignMediaPlanner


class PhotoEngine:
    def __init__(self, settings: Settings, db: Database) -> None:
        policy = PhotoPolicy()
        self.search_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=5), trust_env=False, follow_redirects=False
        )
        self.download_client = httpx.AsyncClient(
            transport=PinnedPhotoTransport(policy),
            timeout=httpx.Timeout(15, connect=5),
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        )
        self.storage = LocalMediaStorage(settings.media_storage_dir)
        self.planner = CampaignMediaPlanner(
            db.sessions,
            SearchCache(
                db.sessions,
                PixabayPhotoProvider(settings.pixabay_api_key, self.search_client),
                settings.photo_search_cache_hours,
            ),
            PhotoDownloader(self.download_client, policy),
            self.storage,
            settings,
            policy,
        )

    async def aclose(self) -> None:
        await self.search_client.aclose()
        await self.download_client.aclose()
