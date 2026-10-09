"""Preview-only, embedding-first retrieval from other indexed communities."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select

from dropgrid.db.models import Community, CommunityReferencePhoto, GridCommunity, utcnow
from dropgrid.integrations.vk.errors import VKError
from dropgrid.photos.archive import VKArchivePhotoProvider, archive_identity
from dropgrid.photos.concepts import car_model
from dropgrid.photos.domain import PhotoError, PhotoPolicy, normalize_category
from dropgrid.photos.pool import PoolCandidate, deduplicate_pool, rank_pool
from dropgrid.photos.progress import emit
from dropgrid.photos.rotation import community_usage, recently_used
from dropgrid.photos.visual import CommunityVisualRanker, deserialize_embedding
from dropgrid.photos.visual_library import VisualLibrary

MAX_INDEXED_ROWS = 2000
MAX_SHORTLIST = 48
MAX_MATERIALIZED = 16


@dataclass
class CategoryLibraryStats:
    category: str = ""
    communities_indexed: int = 0
    reference_photos: int = 0
    archive_photos: int = 0
    compatible_embeddings: int = 0
    candidate_rows: int = 0
    shortlist: int = 0
    source_communities: list[UUID] = field(default_factory=list)


def context_matches(
    target_category: str | None,
    target_hint: str | None,
    target_name: str,
    source_category: str | None,
    source_hint: str | None,
    source_name: str,
) -> bool:
    target_model = car_model(target_hint) or car_model(target_name) or car_model(target_category)
    source_model = car_model(source_hint) or car_model(source_name) or car_model(source_category)
    if target_model:
        return source_model == target_model
    category = normalize_category(target_category)
    return bool(category and category == normalize_category(source_category))


class VKCategoryArchivePhotoProvider:
    name = "vk_category_archive"

    def __init__(self, archive: VKArchivePhotoProvider, visual: VisualLibrary) -> None:
        self.archive, self.visual, self.sessions = archive, visual, visual.sessions

    async def shortlist(
        self, target_id: UUID, category: str | None, hint: str | None, grid_id: UUID | None = None
    ) -> tuple[list[PoolCandidate], CategoryLibraryStats]:
        stats = CategoryLibraryStats(
            category=car_model(hint) or car_model(category) or normalize_category(category)
        )
        if not self.visual.embedder:
            return [], stats
        refs, _, _ = await self.visual.references(target_id)
        if not refs:
            return [], stats
        async with self.sessions() as session:
            target = await session.get(Community, target_id)
            if not target:
                raise PhotoError("category_library_unavailable")
            query = (
                select(Community, GridCommunity)
                .outerjoin(
                    GridCommunity,
                    (GridCommunity.community_id == Community.id)
                    & (GridCommunity.grid_id == grid_id),
                )
                .where(Community.id != target_id, Community.is_active.is_(True))
                .order_by(Community.id)
                .limit(600)
            )
            sources = list((await session.execute(query)).all())
            ids = {
                community.id
                for community, relation in sources
                if context_matches(
                    category,
                    hint,
                    target.name or target.domain,
                    relation.category if relation else community.category,
                    relation.content_hint if relation else None,
                    community.name or community.domain,
                )
            }
            if not ids:
                return [], stats
            rows = list(
                (
                    await session.scalars(
                        select(CommunityReferencePhoto)
                        .where(
                            CommunityReferencePhoto.community_id.in_(ids),
                            CommunityReferencePhoto.enabled.is_(True),
                        )
                        .order_by(
                            CommunityReferencePhoto.posted_at.desc(), CommunityReferencePhoto.id
                        )
                        .limit(MAX_INDEXED_ROWS)
                    )
                ).all()
            )
            usage = await community_usage(session, target_id, utcnow())
        stats.communities_indexed = len({row.community_id for row in rows})
        stats.reference_photos = sum(row.is_style_reference for row in rows)
        stats.archive_photos = sum(row.archive_discovered for row in rows)
        pool = []
        for row in rows:
            if (
                not row.embedding
                or row.embedding_model != self.visual.embedder.model
                or row.embedding_dimensions != self.visual.embedder.dimensions
            ):
                continue
            try:
                vector = deserialize_embedding(
                    row.embedding, row.embedding_model, row.embedding_dimensions
                )
            except PhotoError:
                continue
            stats.compatible_embeddings += 1
            if (
                not row.width
                or not row.height
                or not PhotoPolicy().dimensions_allowed(row.width, row.height)
                or recently_used(
                    usage,
                    self.name,
                    archive_identity(row),
                    row.sha256,
                    row.perceptual_hash,
                    utcnow(),
                )
            ):
                continue
            photo = self.archive.photo(row, category).model_copy(
                update={
                    "provider": self.name,
                    "publication_eligible": False,
                    "license_code": "cross-community-preview-only",
                    "license_name": "Cross-community publication not approved",
                }
            )
            pool.append(
                PoolCandidate(
                    photo,
                    self.name,
                    reference=row,
                    embedding=vector,
                    sha256=row.sha256,
                    perceptual_hash=row.perceptual_hash,
                )
            )
        stats.candidate_rows = len(pool)
        pool = deduplicate_pool(pool)

        def similarity(item: PoolCandidate) -> tuple[float, tuple[str, str]]:
            assert item.embedding
            return -(
                CommunityVisualRanker().score(item.embedding, refs).top_k_mean or 0
            ), item.identity

        pool.sort(key=similarity)
        pool = pool[:MAX_SHORTLIST]
        stats.shortlist = len(pool)
        stats.source_communities = sorted(
            {item.reference.community_id for item in pool if item.reference}, key=str
        )
        return pool, stats

    async def preview(
        self, target_id: UUID, category: str | None, hint: str | None, grid_id: UUID | None = None
    ) -> tuple[list[PoolCandidate], CategoryLibraryStats, list[str]]:
        pool, stats = await self.shortlist(target_id, category, hint, grid_id)
        materialized, warnings = [], []
        selected = pool[:MAX_MATERIALIZED]
        await emit(
            "category_shortlist",
            len(pool),
            len(pool),
            category_candidates=stats.candidate_rows,
            category_shortlist=stats.shortlist,
        )
        for index, item in enumerate(selected):
            await emit("category_materializing", index, len(selected))
            try:
                assert item.reference
                async with asyncio.timeout(15):
                    item.reference = await self.archive.prepare(item.reference)
                if (
                    item.reference.embedding
                    and item.reference.embedding_model
                    and item.reference.embedding_dimensions
                ):
                    item.embedding = deserialize_embedding(
                        item.reference.embedding,
                        item.reference.embedding_model,
                        item.reference.embedding_dimensions,
                    )
                item.sha256, item.perceptual_hash = (
                    item.reference.sha256,
                    item.reference.perceptual_hash,
                )
                materialized.append(item)
            except (PhotoError, OSError, VKError, TimeoutError):
                warnings.append("category_library_photo_unavailable")
        await emit(
            "category_materializing",
            len(selected),
            len(selected),
            category_materialized=len(materialized),
        )
        return (
            await rank_pool(self.visual, target_id, materialized, category),
            stats,
            sorted(set(warnings)),
        )
