"""Bounded normalized preview cache. Never creates publishable media."""

import asyncio
import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import PhotoPreviewCache
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import PhotoCandidate, PhotoError, PhotoQueryPlan
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.photos.pinterest import PinterestPolicy
from dropgrid.photos.pool import PoolCandidate, rank_pool
from dropgrid.photos.progress import emit
from dropgrid.photos.retrieval import RetrievalResult, retrieve_photos
from dropgrid.photos.visual import deserialize_embedding, serialize_embedding
from dropgrid.photos.visual_library import VisualLibrary

MATERIALIZATION_LIMIT = 32


class PinterestPreview:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        cache: SearchCache,
        downloader: PhotoDownloader,
        storage: LocalMediaStorage,
        visual: VisualLibrary,
    ) -> None:
        self.sessions, self.cache, self.downloader, self.storage, self.visual = (
            sessions,
            cache,
            downloader,
            storage,
            visual,
        )
        self.lock = asyncio.Lock()
        self.target_candidates = 32
        self.last_materialized = self.last_embedded = 0
        self.last_retrieval: RetrievalResult | None = None

    async def materialize(self, candidate: PhotoCandidate) -> PoolCandidate:
        # Local lock coalesces downloads; DB uniqueness covers other API processes.
        async with self.lock:
            async with self.sessions() as session:
                row = await session.scalar(
                    select(PhotoPreviewCache).where(
                        PhotoPreviewCache.provider == "pinterest",
                        PhotoPreviewCache.provider_asset_id == candidate.provider_asset_id,
                    )
                )
            data = None
            if row:
                path = self.storage.path(row.storage_key)
                if path.is_file() and path.stat().st_size <= PinterestPolicy().max_output_bytes:
                    data = await asyncio.to_thread(path.read_bytes)
                    if hashlib.sha256(data).hexdigest() != row.sha256:
                        data = None
            if data is None:
                raw = await self.downloader.download(candidate.candidate_download_url)
                normalized = await asyncio.to_thread(normalize_image, raw, PinterestPolicy())
                key = await asyncio.to_thread(self.storage.write, normalized)
                async with self.sessions() as session, session.begin():
                    await session.execute(
                        insert(PhotoPreviewCache)
                        .values(
                            provider="pinterest",
                            provider_asset_id=candidate.provider_asset_id,
                            candidate=candidate.model_dump(mode="json"),
                            storage_key=key,
                            sha256=normalized.sha256,
                            perceptual_hash=normalized.perceptual_hash,
                        )
                        .on_conflict_do_update(
                            index_elements=["provider", "provider_asset_id"],
                            set_={
                                "candidate": candidate.model_dump(mode="json"),
                                "storage_key": key,
                                "sha256": normalized.sha256,
                                "perceptual_hash": normalized.perceptual_hash,
                                "embedding": None,
                                "embedding_model": None,
                                "embedding_dimensions": None,
                            },
                        )
                    )
                    row = await session.scalar(
                        select(PhotoPreviewCache).where(
                            PhotoPreviewCache.provider == "pinterest",
                            PhotoPreviewCache.provider_asset_id == candidate.provider_asset_id,
                        )
                    )
                assert row
                data = await asyncio.to_thread(self.storage.path(row.storage_key).read_bytes)
                if hashlib.sha256(data).hexdigest() != row.sha256:
                    raise PhotoError("preview_storage_unavailable")
            assert row
            embedding, error = None, None
            if self.visual.embedder:
                try:
                    if (
                        row.embedding
                        and row.embedding_model == self.visual.embedder.model
                        and row.embedding_dimensions == self.visual.embedder.dimensions
                    ):
                        embedding = deserialize_embedding(
                            row.embedding, row.embedding_model, row.embedding_dimensions
                        )
                    else:
                        embedding = await self.visual.embedder.embed_image(data)
                        async with self.sessions() as session, session.begin():
                            fresh = await session.get(
                                PhotoPreviewCache, row.id, with_for_update=True
                            )
                            if fresh:
                                (
                                    fresh.embedding,
                                    fresh.embedding_model,
                                    fresh.embedding_dimensions,
                                ) = (
                                    serialize_embedding(embedding),
                                    embedding.model,
                                    embedding.dimensions,
                                )
                except PhotoError as exc:
                    error = exc.code
            return PoolCandidate(
                candidate,
                "pinterest",
                preview_id=row.id,
                embedding=embedding,
                embedding_error=error,
                sha256=row.sha256,
                perceptual_hash=row.perceptual_hash,
            )

    async def compare(
        self, community_id: UUID, plan: PhotoQueryPlan
    ) -> tuple[list[PoolCandidate], int, dict[str, list[str]], list[str]]:
        result = await retrieve_photos(
            self.cache, plan, PinterestPolicy(), target=self.target_candidates, max_candidates=40
        )
        pool = []
        queries = {item.photo.provider_asset_id: item.queries for item in result.items[:100]}
        warnings = list(result.warnings)
        materialization = result.items[:MATERIALIZATION_LIMIT]
        for index, item in enumerate(materialization):
            await emit("pinterest_materializing", index, len(materialization))
            try:
                pool.append(await self.materialize(item.photo))
            except (PhotoError, OSError):
                warnings.append("pinterest_preview_unavailable")
        await emit(
            "pinterest_materializing",
            len(materialization),
            len(materialization),
            pins_materialized=len(pool),
            pins_embedded=sum(item.embedding is not None for item in pool),
        )
        ranked = await rank_pool(self.visual, community_id, pool, plan.category)
        self.last_retrieval = result
        self.last_materialized = len(pool)
        self.last_embedded = sum(item.embedding is not None for item in pool)
        return (
            ranked,
            min(len(result.items), 100),
            queries,
            warnings,
        )
