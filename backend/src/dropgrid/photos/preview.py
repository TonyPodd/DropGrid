"""Bounded source comparison; archive candidates never become MediaAsset here."""

import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from dropgrid.db.models import Community, CommunityContentProfile, GridCommunity, MediaAsset, utcnow
from dropgrid.photos.archive_retrieval import ARCHIVE_STRATA, age_band
from dropgrid.photos.category_archive import CategoryLibraryStats, VKCategoryArchivePhotoProvider
from dropgrid.photos.concepts import car_model
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.photos.domain import normalize_category
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.pool import PoolCandidate, archive_pool, rank_pool
from dropgrid.photos.reference_schemas import (
    ArchiveAgeStratum,
    ArchiveShortlistItem,
    PhotoPreviewInput,
    PhotoPreviewItem,
    PhotoPreviewRead,
    ReferenceMatch,
    VisualEngineRead,
)
from dropgrid.photos.retrieval import RetrievalResult, RetrievedPhoto, retrieve_photos
from dropgrid.photos.timings import collect_timings, stage
from dropgrid.photos.visual_library import VisualLibrary
from dropgrid.services.catalog import ConflictError, get_entity


def preview_item(item: PoolCandidate) -> PhotoPreviewItem:
    assert item.score
    score = item.score
    return PhotoPreviewItem(
        preview_id=item.preview_id,
        publication_eligible=item.photo.publication_eligible,
        pin_url=item.photo.source_page_url if item.photo.provider == "pinterest" else None,
        title=item.photo.title,
        top_references=[
            ReferenceMatch(reference_id=id, similarity=similarity)
            for id, similarity in item.reference_matches
        ],
        media_asset_id=item.asset.id if item.asset else None,
        reference_id=item.reference.id if item.reference else None,
        source_community_id=item.reference.community_id if item.reference else None,
        source_post_id=item.reference.vk_post_id if item.reference else None,
        vk_photo_owner_id=item.reference.vk_photo_owner_id if item.reference else None,
        vk_photo_id=item.reference.vk_photo_id if item.reference else None,
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
    effective_hint = content_hint or car_model(community.name)
    plan = planner.builder.build(
        category, effective_hint, profile.desired_content if profile else None
    )
    pixabay: list[PoolCandidate] = []
    warnings: list[str] = []
    with stage("query_retrieval"):
        retrieval = (
            await retrieve_photos(planner.cache, plan, planner.policy)
            if data.include_pixabay
            else RetrievalResult()
        )
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
    # Keep each lane independent: mixed-pool scoring cannot mutate source scores.
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
    for asset in library if data.include_library else []:
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

    ranked_pinterest: list[PoolCandidate] = []
    pins_retrieved = 0
    pin_queries: dict[str, list[str]] = {}
    pinterest_status = planner.pinterest_status
    if planner.pinterest_preview and data.include_pinterest:
        (
            ranked_pinterest,
            pins_retrieved,
            pin_queries,
            pin_warnings,
        ) = await planner.pinterest_preview.compare(community_id, plan)
        warnings.extend(pin_warnings)
        pinterest_status = (
            "ready"
            if not pin_warnings
            else "search_unavailable"
            if not pins_retrieved
            else "partial"
        )

    def pin_output(item: PoolCandidate) -> PhotoPreviewItem:
        return preview_item(item).model_copy(
            update={"retrieval_queries": pin_queries.get(item.photo.provider_asset_id, [])}
        )

    category_pool: list[PoolCandidate] = []
    library_stats = CategoryLibraryStats(category=category or "")
    if (
        data.include_category_library
        and planner.archive
        and planner.settings.cross_community_reuse_enabled
    ):
        category_pool, library_stats, category_warnings = await VKCategoryArchivePhotoProvider(
            planner.archive, visual
        ).preview(community_id, category, effective_hint, data.grid_id)
        warnings.extend(category_warnings)
    elif data.include_category_library:
        warnings.append("category_library_disabled")
    best = await rank_pool(
        visual,
        community_id,
        [
            replace(item, score=None, reference_matches=[])
            for item in mixed + ranked_pinterest + category_pool
        ],
        category,
    )
    diagnostic_candidates = mixed + ranked_pinterest + category_pool
    embeddings = sum(item.embedding is not None for item in diagnostic_candidates)
    reason = None
    if visual.embedder is None:
        reason = "model_disabled"
    elif any(
        item.embedding_error in {"visual_model_unavailable", "visual_embedding_unavailable"}
        for item in diagnostic_candidates
    ):
        reason = "model_unavailable"
    elif not reference_ids:
        reason = "no_compatible_references"
    elif embeddings < len(diagnostic_candidates) or not embeddings:
        reason = "candidate_embedding_unavailable"
    active = any(
        item.score and item.score.visual_score is not None for item in diagnostic_candidates
    )
    if not active and reason is None:
        reason = "no_compatible_references"
    return PhotoPreviewRead(
        best_matches=[pin_output(i) if i.source == "pinterest" else output(i) for i in best[:32]],
        category_library=[output(i) for i in category_pool[: data.candidate_limit]],
        category_library_stats=asdict(library_stats),
        pinterest_status=pinterest_status,
        pinterest_queries=[v.query for v in plan.variants],
        pinterest_retrieved=pins_retrieved,
        pinterest_embedded=sum(item.embedding is not None for item in ranked_pinterest),
        pinterest=[
            pin_output(i)
            for i in sorted(
                ranked_pinterest,
                key=lambda c: (-(c.score.base_score if c.score else 0), c.identity),
            )[: data.candidate_limit]
        ],
        community_ranked_pinterest=[
            pin_output(i) for i in ranked_pinterest[: data.candidate_limit]
        ],
        visual_engine=VisualEngineRead(
            enabled=visual.embedder is not None,
            model=visual.embedder.model if visual.embedder else None,
            compatible_reference_count=len(reference_ids),
            candidate_embeddings_available=embeddings,
            active=active,
            reason_if_inactive=reason,
        ),
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


async def _timed_preview(
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
                visual_engine=VisualEngineRead(
                    enabled=visual.embedder is not None,
                    model=visual.embedder.model if visual.embedder else None,
                    reason_if_inactive="candidate_embedding_unavailable"
                    if visual.embedder
                    else "model_disabled",
                ),
                pinterest_status=planner.pinterest_status,
            )
        if data.diagnostics and planner.settings.app_env == "development":
            result.timings_ms = {key: round(value, 2) for key, value in timings.items()}
        return result


async def photo_preview(
    planner: CampaignMediaPlanner,
    visual: VisualLibrary,
    community_id: UUID,
    data: PhotoPreviewInput,
) -> PhotoPreviewRead:
    token = uuid4()
    async with planner.sessions() as session, session.begin():
        await get_entity(session, Community, community_id)
        await session.execute(
            insert(CommunityContentProfile)
            .values(community_id=community_id)
            .on_conflict_do_nothing()
        )
        profile = await session.get(CommunityContentProfile, community_id, with_for_update=True)
        assert profile
        for until, code in (
            (profile.sync_lease_until, "reference_sync_in_progress"),
            (profile.archive_lease_until, "archive_sync_in_progress"),
            (profile.preview_lease_until, "preview_in_progress"),
        ):
            if until and until > utcnow():
                raise PhotoConflict(code)
        profile.preview_lease_token, profile.preview_lease_until = (
            token,
            utcnow() + timedelta(seconds=planner.policy.plan_timeout_seconds + 30),
        )
    try:
        return await _timed_preview(planner, visual, community_id, data)
    finally:
        async with planner.sessions() as session, session.begin():
            profile = await session.get(CommunityContentProfile, community_id, with_for_update=True)
            if profile and profile.preview_lease_token == token:
                profile.preview_lease_token = profile.preview_lease_until = None
