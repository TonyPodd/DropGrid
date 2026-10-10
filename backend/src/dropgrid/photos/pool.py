"""Source-neutral ranked pool; archive images remain references until selected."""

import asyncio
import hashlib
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert

from dropgrid.db.models import (
    Community,
    CommunityContentProfile,
    CommunityReferencePhoto,
    GridCommunity,
    MediaAsset,
    MediaProviderImport,
    utcnow,
)
from dropgrid.integrations.vk.errors import VKError
from dropgrid.photos.archive import VKArchivePhotoProvider, archive_identity
from dropgrid.photos.domain import (
    Deduplicator,
    PhotoCandidate,
    PhotoError,
    PhotoPolicy,
    PhotoQueryPlan,
)
from dropgrid.photos.images import NormalizedPhoto
from dropgrid.photos.progress import emit
from dropgrid.photos.rotation import archive_age_penalty, community_usage, recently_used
from dropgrid.photos.timings import stage, timed
from dropgrid.photos.visual import (
    BASE_SCORE_SCALE,
    CommunityVisualRanker,
    RankedPhoto,
    VisualEmbedding,
    VisualScore,
    cosine_similarity,
    deserialize_embedding,
    rank_photo,
)
from dropgrid.photos.visual_library import VisualLibrary

if TYPE_CHECKING:
    from dropgrid.photos.planner import CampaignMediaPlanner


near_duplicate_exclusions: ContextVar[int] = ContextVar("near_duplicate_exclusions", default=0)


@dataclass
class PoolCandidate:
    photo: PhotoCandidate
    source: str
    asset: MediaAsset | None = None
    reference: CommunityReferencePhoto | None = None
    embedding: VisualEmbedding | None = None
    sha256: str | None = None
    perceptual_hash: str | None = None
    score: RankedPhoto | None = None
    age_reuse_score: float = 0
    preview_id: UUID | None = None
    reference_matches: list[tuple[UUID, float]] = field(default_factory=list)
    embedding_error: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return self.photo.provider, self.photo.provider_asset_id

    @property
    def posted_at(self) -> datetime | None:
        return self.reference.posted_at if self.reference else None


def exclude_self_reference(candidate: PoolCandidate, reference: CommunityReferencePhoto) -> bool:
    return bool(
        candidate.reference
        and (candidate.reference.vk_photo_owner_id, candidate.reference.vk_photo_id)
        == (reference.vk_photo_owner_id, reference.vk_photo_id)
        or candidate.sha256
        and candidate.sha256 == reference.sha256
        or Deduplicator().near(candidate.perceptual_hash, reference.perceptual_hash)
    )


def deduplicate_pool(pool: list[PoolCandidate]) -> list[PoolCandidate]:
    # Stable identity, never provider append order, settles duplicates.
    result: list[PoolCandidate] = []
    for item in sorted(pool, key=lambda c: (c.asset is None, c.identity)):
        if any(
            item.identity == old.identity
            or item.reference
            and old.reference
            and (item.reference.vk_photo_owner_id, item.reference.vk_photo_id)
            == (old.reference.vk_photo_owner_id, old.reference.vk_photo_id)
            or item.sha256
            and item.sha256 == old.sha256
            or Deduplicator().near(item.perceptual_hash, old.perceptual_hash)
            for old in result
        ):
            if any(
                Deduplicator().near(item.perceptual_hash, old.perceptual_hash) for old in result
            ):
                near_duplicate_exclusions.set(near_duplicate_exclusions.get() + 1)
            continue
        result.append(item)
    return result


@timed("ranking")
async def rank_pool(
    visual: VisualLibrary,
    community_id: UUID,
    pool: list[PoolCandidate],
    plan: PhotoQueryPlan,
    *,
    now: datetime | None = None,
) -> list[PoolCandidate]:
    now = now or utcnow()
    with stage("reference_loading"):
        async with visual.sessions() as s:
            profile = await s.get(CommunityContentProfile, community_id)
            usage = await community_usage(s, community_id, now)
    refs, _ = await visual.reference_rows(community_id)
    kept = []
    unique_pool = deduplicate_pool(pool)
    for index, item in enumerate(unique_pool):
        await emit("ranking", index, len(unique_pool))
        item.reference_matches = []
        if recently_used(
            usage,
            *item.identity,
            item.sha256,
            item.perceptual_hash,
            now,
            item.asset.id if item.asset else None,
        ):
            continue
        if item.asset and visual.embedder:
            try:
                item.embedding = await visual.asset_embedding(item.asset)
            except (PhotoError, OSError) as error:
                item.embedding_error = (
                    error.code if isinstance(error, PhotoError) else "media_unavailable"
                )
        vectors = []
        if item.embedding:
            for ref in refs:
                if (
                    exclude_self_reference(item, ref)
                    or not ref.embedding
                    or ref.embedding_model != item.embedding.model
                    or ref.embedding_dimensions != item.embedding.dimensions
                ):
                    continue
                try:
                    reference_vector = deserialize_embedding(
                        ref.embedding, ref.embedding_model, ref.embedding_dimensions
                    )
                    vectors.append(reference_vector)
                    item.reference_matches.append(
                        (ref.id, cosine_similarity(item.embedding, reference_vector))
                    )
                except PhotoError:
                    pass
        score = (
            CommunityVisualRanker().score(item.embedding, vectors)
            if item.embedding
            else VisualScore(None, None, 0)
        )
        item.score = rank_photo(
            item.photo,
            plan,
            score,
            profile.desired_content if profile else None,
            profile.avoid_content if profile else None,
            item.asset.usage_count if item.asset else 0,
        )
        item.age_reuse_score = -archive_age_penalty(
            item.posted_at, now, profile.archive_reuse_min_age_days if profile else 180
        )
        kept.append(item)
    await emit("ranking", len(unique_pool), len(unique_pool))
    for item in kept:
        item.reference_matches = sorted(item.reference_matches, key=lambda x: (-x[1], str(x[0])))[
            :5
        ]
        assert item.score
        value = item.score
        penalty_scale = BASE_SCORE_SCALE if value.visual_score is None else 1
        item.score = replace(
            value, final_score=value.final_score + item.age_reuse_score * penalty_scale
        )
    return sorted(
        kept,
        key=lambda c: (
            c.score is None or c.score.visual_score is None,
            -(c.score.final_score if c.score else 0),
            c.identity,
        ),
    )


@timed("archive_preparation")
async def archive_pool(
    provider: VKArchivePhotoProvider | None,
    community_id: UUID,
    warnings: list[str],
    category: str | None = None,
) -> list[PoolCandidate]:
    if provider is None:
        return []
    result = []
    now = utcnow()
    async with provider.collector.sessions() as s:
        usage = await community_usage(s, community_id, now)
        profile = await s.get(CommunityContentProfile, community_id)

    async def prepare_one(row: CommunityReferencePhoto) -> tuple[PoolCandidate | None, str | None]:
        if recently_used(
            usage, "vk_archive", archive_identity(row), row.sha256, row.perceptual_hash, now
        ):
            return None, None
        try:
            async with asyncio.timeout(15):
                row = await provider.prepare(row)
            from dropgrid.photos.references import eligible_for_archive_reuse

            if not profile or not eligible_for_archive_reuse(
                row.posted_at,
                utcnow(),
                profile.archive_reuse_min_age_days,
                profile.archive_reuse_enabled,
                row.enabled,
                profile.archive_reuse_max_age_days,
            ):
                return None, None
            photo = provider.photo(row, category)
            if not PhotoPolicy().candidate_allowed(photo):
                return None, None
            assert row.embedding and row.embedding_model and row.embedding_dimensions
            return PoolCandidate(
                photo,
                "vk_archive",
                reference=row,
                embedding=deserialize_embedding(
                    row.embedding, row.embedding_model, row.embedding_dimensions
                ),
                sha256=row.sha256,
                perceptual_hash=row.perceptual_hash,
            ), None
        except (PhotoError, OSError) as error:
            return None, error.code if isinstance(
                error, PhotoError
            ) else "archive_storage_unavailable"
        except TimeoutError:
            return None, "archive_candidate_timeout"
        except VKError:
            return None, "archive_vk_read_failed"

    rows = await provider.candidates(community_id)
    width = min(3, provider.collector.client.settings.photo_download_concurrency)
    consecutive_failures = 0
    for start in range(0, len(rows), width):
        # At most three preparations in flight, independently of CLIP's single
        # CPU lock. Per-reference cache locks prevent duplicate work, without
        # serializing unrelated CDN requests behind one stalled download.
        prepared = await asyncio.gather(*(prepare_one(row) for row in rows[start : start + width]))
        for candidate, warning in prepared:
            if warning:
                warnings.append(warning)
                consecutive_failures += 1
            elif candidate:
                result.append(candidate)
                consecutive_failures = 0
        if consecutive_failures >= 3:
            warnings.append("archive_failure_bound_reached")
            break
    return result


async def materialize_archive(
    provider: VKArchivePhotoProvider,
    planner: "CampaignMediaPlanner",
    candidate: PoolCandidate,
    category: str,
    *,
    target_id: UUID | None = None,
    grid_id: UUID | None = None,
) -> tuple[MediaAsset, bool]:
    row = candidate.reference
    if row is None:
        raise PhotoError("archive_photo_unavailable")
    cross = candidate.source == "vk_category_archive"
    if cross and (not planner.settings.cross_community_reuse_enabled or target_id is None):
        raise PhotoError("cross_community_reuse_disabled")
    target_id = target_id or row.community_id
    # Revalidate opt-in and the age window after scoring, including concurrent edits.
    async with provider.collector.sessions() as s:
        p = await s.get(CommunityContentProfile, row.community_id)
        fresh = await s.get(CommunityReferencePhoto, row.id)
        if cross:
            from dropgrid.photos.category_archive import context_matches

            source_community = await s.get(Community, row.community_id)
            target = await s.get(Community, target_id)
            relation = await s.get(GridCommunity, (grid_id, row.community_id)) if grid_id else None
            target_relation = await s.get(GridCommunity, (grid_id, target_id)) if grid_id else None
            from dropgrid.photos.concepts import retrieval_hint

            target_hint = retrieval_hint(
                target_relation.content_hint if target_relation else None,
                target.name if target else None,
                target_relation.comment if target_relation else None,
            )
            if (
                not source_community
                or not target
                or not source_community.is_active
                or not target.is_active
                or source_community.id == target.id
                or not context_matches(
                    category,
                    target_hint,
                    target.name or target.domain,
                    relation.category if relation else source_community.category,
                    relation.content_hint if relation else None,
                    source_community.name or source_community.domain,
                )
            ):
                raise PhotoError("category_archive_context_changed")
        from dropgrid.photos.references import eligible_for_archive_reuse

        if (
            not fresh
            or not fresh.enabled
            or (
                not cross
                and (
                    not p
                    or not eligible_for_archive_reuse(
                        fresh.posted_at,
                        utcnow(),
                        p.archive_reuse_min_age_days,
                        p.archive_reuse_enabled,
                        True,
                        p.archive_reuse_max_age_days,
                    )
                )
            )
        ):
            raise PhotoError("archive_policy_changed")
    row = await provider.prepare(fresh)
    assert row.storage_key and row.sha256 and row.perceptual_hash and row.width and row.height
    data = await asyncio.to_thread(provider.collector.storage.path(row.storage_key).read_bytes)
    if hashlib.sha256(data).hexdigest() != row.sha256:
        raise PhotoError("archive_photo_unavailable")
    image = NormalizedPhoto(data, row.sha256, row.perceptual_hash, row.width, row.height)
    photo = candidate.photo if cross else provider.photo(row, category)
    source = candidate.source
    async with planner.sessions() as s, s.begin():
        await s.execute(text("SELECT pg_advisory_xact_lock(748220102)"))
        p = await s.get(CommunityContentProfile, row.community_id, with_for_update=True)
        if not cross and (
            not p
            or not eligible_for_archive_reuse(
                row.posted_at,
                utcnow(),
                p.archive_reuse_min_age_days,
                p.archive_reuse_enabled,
                True,
                p.archive_reuse_max_age_days,
            )
        ):
            raise PhotoError("archive_policy_changed")
        usage = await community_usage(s, target_id, utcnow())
        if recently_used(
            usage, source, archive_identity(row), row.sha256, row.perceptual_hash, utcnow()
        ):
            raise PhotoError("community_media_cooldown")
        asset = await s.scalar(
            select(MediaAsset)
            .outerjoin(MediaProviderImport, MediaProviderImport.media_asset_id == MediaAsset.id)
            .where(
                or_(
                    MediaAsset.sha256 == row.sha256,
                    (MediaAsset.provider == source)
                    & (MediaAsset.provider_asset_id == archive_identity(row)),
                    (MediaProviderImport.provider == source)
                    & (MediaProviderImport.provider_asset_id == archive_identity(row)),
                )
            )
        )
        if asset is None:
            assets = (
                await s.scalars(
                    select(MediaAsset)
                    .where(MediaAsset.perceptual_hash.is_not(None))
                    .order_by(MediaAsset.id)
                )
            ).all()
            asset = next(
                (a for a in assets if Deduplicator().near(a.perceptual_hash, row.perceptual_hash)),
                None,
            )
        created = asset is None
        if asset and not planner.eligible(asset):
            raise PhotoError("duplicate_not_reusable")
        if asset is None:
            key = await asyncio.to_thread(planner.storage.write, image)
            asset = MediaAsset(
                storage_key=key,
                source_url=photo.source_page_url,
                category=category,
                tags=list(photo.tags),
                provider=source,
                provider_asset_id=archive_identity(row),
                license_code=photo.license_code,
                license_name=photo.license_name,
                license_url=photo.license_url,
                width=row.width,
                height=row.height,
                mime_type="image/jpeg",
                byte_size=len(data),
                sha256=row.sha256,
                perceptual_hash=row.perceptual_hash,
                visual_embedding=row.embedding,
                visual_embedding_model=row.embedding_model,
                visual_embedding_dimensions=row.embedding_dimensions,
            )
            s.add(asset)
            await s.flush()
        await s.execute(
            insert(MediaProviderImport)
            .values(
                provider=source,
                provider_asset_id=archive_identity(row),
                media_asset_id=asset.id,
                source_url=photo.source_page_url,
                creator_name="",
                license_code=photo.license_code,
                source_community_id=row.community_id,
                source_post_id=row.vk_post_id,
                source_photo_owner_id=row.vk_photo_owner_id,
                source_photo_id=row.vk_photo_id,
                source_posted_at=row.posted_at,
                source_sha256=row.sha256,
                source_perceptual_hash=row.perceptual_hash,
                source_embedding=row.embedding.hex() if row.embedding else None,
                source_embedding_model=row.embedding_model,
                source_embedding_dimensions=row.embedding_dimensions,
            )
            .on_conflict_do_nothing()
        )
        return asset, created
