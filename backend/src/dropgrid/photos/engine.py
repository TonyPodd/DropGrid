"""Lifespan-owned pools and provider-neutral composition."""

import asyncio

import httpx

from dropgrid.config import Settings
from dropgrid.db.session import Database
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import PhotoPolicy
from dropgrid.photos.download import PhotoDownloader, PinnedPhotoTransport
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.pixabay import PixabayPhotoProvider
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.references import VKReferencePolicy
from dropgrid.photos.visual import OnnxCLIPEmbedder, VisualEmbedder
from dropgrid.photos.visual_library import VisualLibrary


class PhotoEngine:
    def __init__(self, settings: Settings, db: Database) -> None:
        policy = PhotoPolicy(timeout_seconds=settings.photo_download_timeout_seconds)
        download_slots = asyncio.Semaphore(settings.photo_download_concurrency)
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
        self.embedder: VisualEmbedder | None = (
            OnnxCLIPEmbedder(settings.visual_model_path)
            if settings.visual_embedding_enabled
            else None
        )
        self.visual = VisualLibrary(db.sessions, self.storage, self.embedder)
        reference_policy = VKReferencePolicy(
            timeout_seconds=settings.photo_download_timeout_seconds
        )
        self.reference_client = httpx.AsyncClient(
            transport=PinnedPhotoTransport(reference_policy),
            timeout=httpx.Timeout(15, connect=5),
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        )
        self.reference_downloader = PhotoDownloader(
            self.reference_client, reference_policy, slots=download_slots
        )
        self.reference_storage = LocalMediaStorage(settings.media_storage_dir / "references")
        self.planner = CampaignMediaPlanner(
            db.sessions,
            SearchCache(
                db.sessions,
                PixabayPhotoProvider(settings.pixabay_api_key, self.search_client),
                settings.photo_search_cache_hours,
            ),
            PhotoDownloader(self.download_client, policy, slots=download_slots),
            self.storage,
            settings,
            policy,
            self.visual,
        )

    async def aclose(self) -> None:
        await self.search_client.aclose()
        await self.download_client.aclose()
        await self.reference_client.aclose()
