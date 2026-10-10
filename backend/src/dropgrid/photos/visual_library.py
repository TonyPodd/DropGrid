"""Cached candidate vectors and community scoring, with no provider or VK calls."""

import asyncio
import hashlib
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import CommunityContentProfile, CommunityReferencePhoto, MediaAsset, utcnow
from dropgrid.photos.density import assess_references
from dropgrid.photos.domain import PhotoError, PhotoQueryPlan
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.timings import timed
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

    async def reference_rows(
        self, community_id: UUID
    ) -> tuple[list[CommunityReferencePhoto], CommunityContentProfile | None]:
        async with self.sessions() as s:
            profile = await s.get(CommunityContentProfile, community_id)
            rows = list(
                (
                    await s.scalars(
                        select(CommunityReferencePhoto)
                        .where(
                            CommunityReferencePhoto.community_id == community_id,
                            CommunityReferencePhoto.enabled.is_(True),
                            CommunityReferencePhoto.is_style_reference.is_(True),
                            CommunityReferencePhoto.posted_at >= utcnow() - timedelta(days=180),
                        )
                        .order_by(
                            CommunityReferencePhoto.posted_at.desc(), CommunityReferencePhoto.id
                        )
                        .limit(300)
                    )
                ).all()
            )
        assessments, _ = assess_references(rows, self.embedder)
        core = {a.id for a in assessments if a.role == "core"}
        return [row for row in rows if row.id in core][
            : (profile.reference_target_count if profile else 100)
        ], profile

    @timed("reference_loading")
    async def references(
        self, community_id: UUID
    ) -> tuple[list[VisualEmbedding], list[UUID], CommunityContentProfile | None]:
        rows, profile = await self.reference_rows(community_id)
        _, vectors = assess_references(rows, self.embedder)
        return list(vectors.values()), list(vectors), profile

    async def rank_assets(
        self, community_id: UUID, assets: list[MediaAsset], plan: PhotoQueryPlan
    ) -> dict[UUID, RankedPhoto]:
        from dropgrid.photos.planner import CampaignMediaPlanner

        references, _, profile = await self.references(community_id)
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
                plan,
                visual,
                profile.desired_content if profile else None,
                profile.avoid_content if profile else None,
                asset.usage_count,
            )
        return result
