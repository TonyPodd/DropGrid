"""Bounded source comparison; archive candidates never become MediaAsset here."""

import asyncio
from dataclasses import replace
from uuid import UUID

from sqlalchemy import select

from dropgrid.db.models import Community, CommunityContentProfile, GridCommunity, MediaAsset, utcnow
from dropgrid.photos.archive_retrieval import ARCHIVE_STRATA, age_band
from dropgrid.photos.domain import normalize_category
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.pool import PoolCandidate, archive_pool, rank_pool
from dropgrid.photos.reference_schemas import (
    ArchiveAgeStratum,
    ArchiveShortlistItem,
    PhotoPreviewInput,
    PhotoPreviewItem,
    PhotoPreviewRead,
)
from dropgrid.photos.retrieval import RetrievedPhoto, retrieve_photos
from dropgrid.photos.timings import collect_timings, stage
from dropgrid.photos.visual_library import VisualLibrary
from dropgrid.services.catalog import ConflictError, get_entity


def preview_item(item: PoolCandidate) -> PhotoPreviewItem:
    assert item.score
    score = item.score
    return PhotoPreviewItem(
        media_asset_id=item.asset.id if item.asset else None,
        reference_id=item.reference.id if item.reference else None,
        source=item.source,
        provider=item.photo.provider,
        source_identity=item.photo.provider_asset_id,
        original_posted_at=item.posted_at,
        age_days=(utcnow() - item.posted_at).total_seconds() / 86400 if item.posted_at else None,
        age_reuse_score=item.age_reuse_score,
        base_score=score.base_score,
        visual_score=score.visual_score,
        final_score=score.final_score,
        best_similarity=score.best_similarity,
        reference_count=score.reference_count,
    )


async def _photo_preview(
    planner: CampaignMediaPlanner,
    visual: VisualLibrary,
    community_id: UUID,
    data: PhotoPreviewInput,
) -> PhotoPreviewRead:
    async with planner.sessions() as session:
        community = await get_entity(session, Community, community_id)
        category = community.category
        comment = content_hint = None
        profile = await session.get(CommunityContentProfile, community_id)
        if data.grid_id:
            relation = await session.get(GridCommunity, (data.grid_id, community_id))
            if relation is None:
                raise ConflictError("Community is not in supplied grid")
            category = relation.category
            comment, content_hint = relation.comment, relation.content_hint
    plan = planner.builder.build(
        category, content_hint, profile.desired_content if profile else None
    )
    pixabay: list[PoolCandidate] = []
    warnings: list[str] = []
    with stage("query_retrieval"):
        retrieval = await retrieve_photos(planner.cache, plan, planner.policy)
    warnings.extend(retrieval.warnings)
    provenance: dict[UUID, list[str]] = {}

    async def import_one(retrieved: RetrievedPhoto) -> tuple[RetrievedPhoto, MediaAsset | None]:
        candidate = retrieved.photo
        async with planner.sessions() as session:
            known = await session.scalar(
                select(MediaAsset).where(
                    MediaAsset.provider == candidate.provider,
                    MediaAsset.provider_asset_id == candidate.provider_asset_id,
                )
            )
        asset: MediaAsset | None
        if known and planner.eligible(known, plan.sensitive):
            asset = known
        else:
            asset, _, warning = await planner._import(candidate, plan.category, plan.sensitive)
            if warning:
                warnings.append(warning)
        return retrieved, asset

    imports = await asyncio.gather(
        *(import_one(item) for item in retrieval.items[: data.candidate_limit])
    )
    for retrieved, asset in imports:
        if asset:
            produced = provenance.setdefault(asset.id, [])
            produced.extend(q for q in retrieved.queries if q not in produced)
            if all(item.asset and item.asset.id != asset.id for item in pixabay):
                pixabay.append(
                    PoolCandidate(
                        planner._asset_candidate(asset),
                        "pixabay",
                        asset=asset,
                        sha256=asset.sha256,
                        perceptual_hash=asset.perceptual_hash,
                    )
                )
    ranked_pixabay = await rank_pool(visual, community_id, pixabay, category)
    # Scoring the mixed pool may choose legacy fallback. Keep the Pixabay-only
    # comparison independent so its scores/order cannot be overwritten.
    pool = [replace(item, score=None, age_reuse_score=0) for item in pixabay]
    async with planner.sessions() as session:
        library = (
            await session.scalars(
                select(MediaAsset)
                .where(MediaAsset.enabled.is_(True), MediaAsset.provider != "vk_archive")
                .order_by(MediaAsset.id)
                .limit(100)
            )
        ).all()
    for asset in library:
        if (
            normalize_category(asset.category) == plan.category
            and planner.eligible(asset, plan.sensitive)
            and all(item.asset and item.asset.id != asset.id for item in pool)
        ):
            pool.append(
                PoolCandidate(
                    planner._asset_candidate(asset),
                    "library",
                    asset=asset,
                    sha256=asset.sha256,
                    perceptual_hash=asset.perceptual_hash,
                )
            )
        if len(pool) >= data.candidate_limit * 2:
            break
    archive = []
    if data.include_archive:
        archive = await archive_pool(planner.archive, community_id, warnings, category)
        pool += archive
    strata = []
    shortlist = []
    async with planner.sessions() as session:
        profile = await session.get(CommunityContentProfile, community_id)
    if profile:
        lower, upper = profile.archive_reuse_min_age_days, profile.archive_reuse_max_age_days
        now = utcnow()
        shortlist = [
            ArchiveShortlistItem(
                source_identity=item.photo.provider_asset_id,
                age_days=(now - item.posted_at).total_seconds() / 86400,
            )
            for item in archive
            if item.posted_at
        ]
        width = (upper - lower) / ARCHIVE_STRATA
        strata = [
            ArchiveAgeStratum(
                min_age_days=lower + i * width,
                max_age_days=lower + (i + 1) * width,
                candidate_count=sum(
                    age_band(item.age_days, lower, upper) == i for item in shortlist
                ),
            )
            for i in range(ARCHIVE_STRATA)
        ]
    mixed = await rank_pool(visual, community_id, pool, category)
    _, reference_ids, _ = await visual.references(community_id)
    if not reference_ids:
        warnings.append("visual_references_unavailable")

    def unique_lane(items: list[PoolCandidate]) -> list[PoolCandidate]:
        seen: set[tuple[str, str]] = set()
        result = []
        for item in items:
            if item.identity not in seen:
                result.append(item)
                seen.add(item.identity)
        return result

    ranked_pixabay = unique_lane(ranked_pixabay)
    mixed = unique_lane(mixed)

    def output(item: PoolCandidate) -> PhotoPreviewItem:
        return preview_item(item).model_copy(
            update={"retrieval_queries": provenance.get(item.asset.id, []) if item.asset else []}
        )

    return PhotoPreviewRead(
        community_id=community_id,
        category=category,
        comment=comment,
        content_hint=content_hint,
        desired_content=profile.desired_content if profile else None,
        avoid_content=profile.avoid_content if profile else None,
        generated_queries=[v.query for v in plan.variants],
        references=reference_ids[:20],
        category_only=[
            output(i)
            for i in sorted(
                ranked_pixabay, key=lambda c: (-(c.score.base_score if c.score else 0), c.identity)
            )
        ],
        community_aware=[output(i) for i in ranked_pixabay],
        archive_age_strata=strata,
        archive_shortlist=shortlist,
        mixed_source=[output(i) for i in mixed[:12]],
        warnings=sorted(set(warnings)),
    )


async def photo_preview(
    planner: CampaignMediaPlanner,
    visual: VisualLibrary,
    community_id: UUID,
    data: PhotoPreviewInput,
) -> PhotoPreviewRead:
    with collect_timings() as timings:
        try:
            async with asyncio.timeout(planner.policy.plan_timeout_seconds):
                result = await _photo_preview(planner, visual, community_id, data)
        except TimeoutError:
            result = PhotoPreviewRead(
                community_id=community_id,
                category=None,
                references=[],
                category_only=[],
                community_aware=[],
                mixed_source=[],
                warnings=["photo_preview_timeout"],
            )
        if data.diagnostics and planner.settings.app_env == "development":
            result.timings_ms = {key: round(value, 2) for key, value in timings.items()}
        return result
