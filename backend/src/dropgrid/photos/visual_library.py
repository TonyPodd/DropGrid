"""Cached candidate vectors and community scoring, with no provider or VK calls."""

import asyncio
import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import CommunityContentProfile, CommunityReferencePhoto, MediaAsset
from dropgrid.photos.domain import PhotoError, PhotoQueryBuilder
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.visual import (
    CommunityVisualRanker,
    RankedPhoto,
    VisualEmbedder,
    VisualEmbedding,
    VisualScore,
    deserialize_embedding,
    rank_photo,
    serialize_embedding,
)


class VisualLibrary:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalMediaStorage,
        embedder: VisualEmbedder | None,
    ) -> None:
        self.sessions, self.storage, self.embedder = sessions, storage, embedder
        self.lock = asyncio.Lock()

    async def asset_embedding(self, asset: MediaAsset) -> VisualEmbedding | None:
        if self.embedder is None:
            return None
        async with self.lock:
            async with self.sessions() as s:
                current = await s.get(MediaAsset, asset.id)
                if not current or not current.enabled:
                    return None
                blob, model, dims = (
                    current.visual_embedding,
                    current.visual_embedding_model,
                    current.visual_embedding_dimensions,
                )
                key, sha = current.storage_key, current.sha256
            if blob and model == self.embedder.model and dims == self.embedder.dimensions:
                try:
                    return deserialize_embedding(blob, model, dims)
                except PhotoError:
                    pass
            path = self.storage.path(key)
            if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                raise PhotoError("media_unavailable")
            data = await asyncio.to_thread(path.read_bytes)
            if not sha or hashlib.sha256(data).hexdigest() != sha:
                raise PhotoError("media_unavailable")
            vector = await self.embedder.embed_image(data)
            async with self.sessions() as s, s.begin():
                fresh = await s.get(MediaAsset, asset.id, with_for_update=True)
                if not fresh or fresh.sha256 != sha or not fresh.enabled:
                    return None
                fresh.visual_embedding = serialize_embedding(vector)
                fresh.visual_embedding_model, fresh.visual_embedding_dimensions = (
                    vector.model,
                    vector.dimensions,
                )
            return vector

    async def references(
        self, community_id: UUID
    ) -> tuple[list[VisualEmbedding], list[UUID], CommunityContentProfile | None]:
        async with self.sessions() as s:
            profile = await s.get(CommunityContentProfile, community_id)
            rows = (
                await s.scalars(
                    select(CommunityReferencePhoto)
                    .where(CommunityReferencePhoto.community_id == community_id)
                    .order_by(CommunityReferencePhoto.posted_at.desc(), CommunityReferencePhoto.id)
                    .limit(profile.reference_target_count if profile else 100)
                )
            ).all()
        vectors = []
        ids = []
        for row in rows:
            if (
                self.embedder
                and row.embedding
                and row.embedding_model == self.embedder.model
                and row.embedding_dimensions == self.embedder.dimensions
            ):
                try:
                    vector = deserialize_embedding(
                        row.embedding, row.embedding_model, row.embedding_dimensions
                    )
                except PhotoError:
                    continue
                vectors.append(vector)
                ids.append(row.id)
        return vectors, ids, profile

    async def rank_assets(
        self, community_id: UUID, assets: list[MediaAsset], category: str | None
    ) -> dict[UUID, RankedPhoto]:
        from dropgrid.photos.planner import CampaignMediaPlanner

        references, _, profile = await self.references(community_id)
        search = PhotoQueryBuilder().build(category).variants[0]
        result = {}
        for asset in assets:
            visual = VisualScore(None, None, 0)
            if references:
                try:
                    vector = await self.asset_embedding(asset)
                    if vector:
                        visual = CommunityVisualRanker().score(vector, references)
                except (PhotoError, OSError):
                    pass
            result[asset.id] = rank_photo(
                CampaignMediaPlanner._asset_candidate(asset),
                search,
                visual,
                profile.desired_content if profile else None,
                profile.avoid_content if profile else None,
                asset.usage_count,
            )
        if any(score.visual_score is None for score in result.values()):
            # Never mix normalized V2 scores with unnormalized legacy scores.
            result = {
                asset.id: rank_photo(
                    CampaignMediaPlanner._asset_candidate(asset),
                    search,
                    VisualScore(None, None, 0),
                    usage=asset.usage_count,
                )
                for asset in assets
            }
        return result
