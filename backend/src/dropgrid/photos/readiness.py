"""Safe local preparation summary; no credentials, captions or external payloads."""

from collections import Counter
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.config import Settings
from dropgrid.db.models import (
    Campaign,
    CampaignPreparationJob,
    Community,
    CommunityReferencePhoto,
    GridCommunity,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
    utcnow,
)
from dropgrid.photos.visual import VisualEmbedder
from dropgrid.services.account_pools import CATEGORY_UNASSIGNED, distribute, pool
from dropgrid.services.catalog import get_entity
from dropgrid.services.category_genders import placements
from dropgrid.services.sending import usable_account


async def report(
    session: AsyncSession,
    campaign_id: UUID,
    settings: Settings,
    embedder: VisualEmbedder | None = None,
) -> dict[str, object]:
    campaign = await get_entity(session, Campaign, campaign_id)
    grid = (
        (
            await session.execute(
                select(Community)
                .join(GridCommunity)
                .where(GridCommunity.grid_id == campaign.grid_id)
            )
        )
        .scalars()
        .all()
    )
    available = sum(
        c.is_active and c.resolution_status == "resolved" and bool(c.vk_group_id) for c in grid
    )
    rows = (
        await session.execute(
            select(Submission, Community)
            .join(Community)
            .where(Submission.campaign_id == campaign_id)
        )
    ).all()
    with_core: set[UUID] = set()
    if embedder:
        with_core = set(
            (
                await session.scalars(
                    select(CommunityReferencePhoto.community_id)
                    .where(
                        CommunityReferencePhoto.community_id.in_([r.community_id for r, _ in rows]),
                        CommunityReferencePhoto.enabled.is_(True),
                        CommunityReferencePhoto.is_style_reference.is_(True),
                        CommunityReferencePhoto.reference_role == "core",
                        CommunityReferencePhoto.embedding.is_not(None),
                        CommunityReferencePhoto.storage_key.is_not(None),
                        CommunityReferencePhoto.embedding_model == embedder.model,
                        CommunityReferencePhoto.embedding_dimensions == embedder.dimensions,
                        CommunityReferencePhoto.posted_at
                        >= utcnow() - timedelta(days=settings.campaign_reference_recent_days),
                    )
                    .distinct()
                )
            ).all()
        )
    assets = Counter(row.media_asset_id for row, _ in rows if row.media_asset_id)
    sources = Counter(row.photo_source or "library" for row, _ in rows if row.media_asset_id)
    attention = Counter(flag for row, _ in rows for flag in row.photo_attention)
    selections = (
        await session.scalars(
            select(PhotoSelectionSession)
            .where(PhotoSelectionSession.campaign_id == campaign_id)
            .order_by(PhotoSelectionSession.created_at.desc(), PhotoSelectionSession.id.desc())
        )
    ).all()
    latest: dict[UUID, PhotoSelectionSession] = {}
    for selection in selections:
        latest.setdefault(selection.submission_id, selection)
    candidates = (
        (
            await session.scalars(
                select(PhotoSelectionCandidate).where(
                    PhotoSelectionCandidate.selection_session_id.in_(
                        [r.id for r in latest.values()]
                    )
                )
            )
        ).all()
        if latest
        else []
    )
    chosen = {s.id: s.proposed_rank for s in latest.values()}
    unavailable_selections = {
        latest[r.id].id
        for r, _ in rows
        if r.id in latest and "pinterest_unavailable" in r.photo_attention
    }
    fallback = sum(
        c.provider != "pinterest"
        and c.rank == chosen[c.selection_session_id]
        and (
            c.selection_session_id in unavailable_selections
            or any(
                d.provider == "pinterest" and d.selection_session_id == c.selection_session_id
                for d in candidates
            )
        )
        for c in candidates
    )
    accounts = [
        (a, quota, priority)
        for a, quota, priority in await pool(session, campaign_id, settings)
        if usable_account(a)
    ]
    allocation_rows: list[tuple[Submission, Community]] = (
        [(r, c) for r, c in rows] if rows else [(Submission(id=c.id), c) for c in grid]
    )
    allocations, failures = distribute(
        [
            (r, c)
            for r, c in allocation_rows
            if c.is_active and c.resolution_status == "resolved" and c.vk_group_id
        ],
        accounts,
        await placements(session, campaign.grid_id),
    )
    job = await session.scalar(
        select(CampaignPreparationJob).where(CampaignPreparationJob.campaign_id == campaign_id)
    )
    started_at = (
        datetime.fromisoformat(str((job.result or {})["run_started_at"]))
        if job and (job.result or {}).get("run_started_at")
        else (job.created_at if job else utcnow())
    )
    duration = (
        ((job.updated_at if job.state == "ready" else utcnow()) - started_at).total_seconds()
        if job
        else None
    )
    return {
        "campaign_id": str(campaign_id),
        "is_dry_run": campaign.is_dry_run,
        "state": job.state if job else None,
        "total_grid_rows": len(grid),
        "available": available,
        "unavailable": len(grid) - available,
        "scope": len(rows) if rows else available,
        "prepared": sum(assets.values()),
        "failed": sum(
            r.media_asset_id is None
            and (bool(job and job.state == "ready") or "preparation_error" in r.photo_attention)
            for r, _ in rows
        ),
        "pending": sum(
            r.media_asset_id is None
            and not (bool(job and job.state == "ready") or "preparation_error" in r.photo_attention)
            for r, _ in rows
        ),
        "needs_attention": sum(bool(r.photo_attention) for r, _ in rows),
        "media_assigned": sum(assets.values()),
        "sources": dict(sources),
        "attention": dict(attention),
        "pinterest_fallback_count": fallback,
        "pinterest_unavailable": attention["pinterest_unavailable"],
        "without_core_refs": sum(r.community_id not in with_core for r, _ in rows),
        "warmup_warnings": (job.result or {}).get("warmup_warnings", {}) if job else {},
        "small_candidate_pools": attention["small_candidate_pool"],
        "unique_media_assets": len(assets),
        "reused_assignments": sum(v - 1 for v in assets.values()),
        "max_reuse": max(assets.values(), default=0),
        "max_reuse_policy": settings.photo_max_reuse_per_asset,
        "reuse_policy_respected": max(assets.values(), default=0)
        <= settings.photo_max_reuse_per_asset,
        "near_duplicate_exclusions": (job.result or {}).get("near_duplicate_exclusions", 0)
        if job
        else 0,
        "duration_seconds": duration,
        "queue_wait_seconds": (job.result or {}).get("queue_wait_seconds") if job else None,
        "timings": {key: (job.result or {}).get(key) for key in ("warmup_seconds", "photo_seconds")}
        if job
        else {},
        "accounts_ready": len(accounts),
        "real_capacity": sum(quota for _, quota, _ in accounts),
        "account_assigned": len(allocations),
        "account_unassigned": len(failures),
        "capacity_shortfall": sum(
            reason == "account_capacity_exhausted" for reason in failures.values()
        ),
        "gender_shortfall": sum(
            reason == "account_gender_mismatch" for reason in failures.values()
        ),
        "category_unassigned": sum(reason == CATEGORY_UNASSIGNED for reason in failures.values()),
        "write_attempts": sum(r.attempt_count for r, _ in rows),
        "submitted": sum(r.submitted_at is not None for r, _ in rows),
    }


async def reuse_audit(session: AsyncSession, campaign_id: UUID) -> dict[str, object]:
    """Audit source provenance of latest snapshots, not ambiguous asset aliases."""
    from dropgrid.db.models import CommunityContentProfile, MediaAsset
    from dropgrid.photos.references import eligible_for_archive_reuse
    from dropgrid.photos.rotation import community_usage, recently_used

    choices = list(
        (
            await session.scalars(
                select(PhotoSelectionSession)
                .where(PhotoSelectionSession.campaign_id == campaign_id)
                .distinct(PhotoSelectionSession.submission_id)
                .order_by(
                    PhotoSelectionSession.submission_id,
                    PhotoSelectionSession.created_at.desc(),
                    PhotoSelectionSession.id.desc(),
                )
            )
        ).all()
    )
    own = same = cross = 0
    violations: list[str] = []
    for choice in choices:
        c = await session.get(
            PhotoSelectionCandidate, (choice.id, choice.chosen_rank or choice.proposed_rank)
        )
        if not c or c.provider not in {"vk_archive", "vk_category_archive"}:
            continue
        reference = (
            await session.get(CommunityReferencePhoto, c.reference_id) if c.reference_id else None
        )
        source = reference.community_id if reference else c.candidate.get("source_community_id")
        same_source = str(source) == str(choice.community_id)
        same += int(same_source)
        if c.provider == "vk_category_archive":
            cross += 1
            if source is None or same_source:
                violations.append("category_source_target_mismatch")
        else:
            own += 1
            profile = await session.get(CommunityContentProfile, choice.community_id)
            if not same_source:
                violations.append("own_source_target_mismatch")
            if (
                not reference
                or not profile
                or not eligible_for_archive_reuse(
                    reference.posted_at,
                    utcnow(),
                    profile.archive_reuse_min_age_days,
                    profile.archive_reuse_enabled,
                    reference.enabled,
                    profile.archive_reuse_max_age_days,
                )
            ):
                violations.append("own_archive_age_window")
        asset = await session.get(MediaAsset, c.media_asset_id) if c.media_asset_id else None
        if asset and recently_used(
            await community_usage(session, choice.community_id, utcnow()),
            asset.provider or "library",
            asset.provider_asset_id or str(asset.id),
            asset.sha256,
            asset.perceptual_hash,
            utcnow(),
            asset.id,
        ):
            violations.append("target_usage_cooldown")
    return {
        "own_archive_selected": own,
        "same_source_community_selected": same,
        "cross_community_selected": cross,
        "violations": dict(Counter(violations)),
        "invariants_pass": not violations,
    }
