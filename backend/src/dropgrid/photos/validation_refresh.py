"""Explicit bounded validation refresh; existing production assignments stay fixed."""

import asyncio
from uuid import UUID

from dropgrid.db.models import Campaign, CommunityReferencePhoto, MediaAsset, Submission
from dropgrid.photos.domain import PhotoCandidate
from dropgrid.photos.learning import number
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.pool import PoolCandidate
from dropgrid.photos.preview import _photo_preview
from dropgrid.photos.reference_schemas import PhotoPreviewInput
from dropgrid.photos.review import latest_selection, snapshot
from dropgrid.photos.visual import RankedPhoto
from dropgrid.photos.visual_library import VisualLibrary
from dropgrid.services.catalog import ConflictError, get_entity


async def refresh_snapshot(
    planner: CampaignMediaPlanner, visual: VisualLibrary, submission_id: UUID
) -> UUID:
    if planner.settings.vk_write_enabled or planner.settings.vk_test_allowed_community_ids:
        raise ConflictError("Validation refresh requires VK writes disabled and empty allowlist")
    from dropgrid.db.models import PhotoSelectionCandidate

    async with planner.sessions() as db:
        submission = await get_entity(db, Submission, submission_id)
        campaign = await get_entity(db, Campaign, submission.campaign_id)
        previous = await latest_selection(db, submission_id)
        old = await db.get(PhotoSelectionCandidate, (previous.id, previous.proposed_rank))
        if campaign.status.value != "ready" or previous.confirmed_at or not old:
            raise ConflictError("Only unconfirmed ready snapshots can be refreshed")
        if submission.media_asset_id is None:
            raise ConflictError("Prepared media required")
        asset = await get_entity(db, MediaAsset, submission.media_asset_id)
        reference = (
            await db.get(CommunityReferencePhoto, old.reference_id) if old.reference_id else None
        )
        winner = PoolCandidate(
            PhotoCandidate.model_validate(
                {k: v for k, v in old.candidate.items() if k in PhotoCandidate.model_fields}
            ),
            old.provider,
            asset=asset,
            reference=reference,
            preview_id=old.preview_id,
            sha256=asset.sha256,
            perceptual_hash=asset.perceptual_hash,
            score=RankedPhoto(
                number(old.features.get("base_score")),
                number(old.features["visual_score"])
                if old.features.get("visual_score") is not None
                else None,
                number(old.features.get("final_score")),
                number(old.features["best_similarity"])
                if old.features.get("best_similarity") is not None
                else None,
                int(number(old.features.get("reference_count"))),
                number(old.features.get("metadata_score")),
                number(old.features.get("quality_score")),
                number(old.features["normalized_visual_score"])
                if old.features.get("normalized_visual_score") is not None
                else None,
            ),
            age_reuse_score=number(old.features.get("age_reuse_score")),
        )
        original_asset = submission.media_asset_id
    pool: list[PoolCandidate] = []
    async with asyncio.timeout(planner.policy.plan_timeout_seconds):
        result = await _photo_preview(
            planner,
            visual,
            submission.community_id,
            PhotoPreviewInput(
                grid_id=campaign.grid_id,
                candidate_limit=12,
                include_category_library=True,
                include_pixabay=False,
            ),
            capture=pool,
        )
    # Keep the original winner's identity/source/score; this is display exploration only.
    from dropgrid.photos.domain import Deduplicator

    pool = [
        i
        for i in pool
        if i.identity != winner.identity
        and not (i.asset and i.asset.id == original_asset)
        and not (i.sha256 and i.sha256 == winner.sha256)
        and not Deduplicator().near(i.perceptual_hash, winner.perceptual_hash)
    ]
    pool.append(winner)
    pool.sort(key=lambda i: (-(i.score.final_score if i.score else 0), i.identity))
    plan = planner.builder.build(result.category, previous.content_hint, result.desired_content)
    async with planner.sessions() as db, db.begin():
        parent = await db.get(Campaign, campaign.id, with_for_update=True)
        fresh = await get_entity(db, Submission, submission_id)
        current = await latest_selection(db, submission_id, True)
        if (
            not parent
            or parent.status.value != "ready"
            or current.id != previous.id
            or current.confirmed_at
            or fresh.media_asset_id != original_asset
        ):
            raise ConflictError("Validation context changed during refresh")
        preserved = (fresh.photo_attention, fresh.photo_source, fresh.ranking_model_version)
        await snapshot(
            db,
            fresh,
            plan,
            pool,
            pool,
            previous.ranking_model_version or "deterministic-v1",
            result.warnings,
        )
        fresh.photo_attention, fresh.photo_source, fresh.ranking_model_version = preserved
        await db.flush()
        return (await latest_selection(db, submission_id)).id
