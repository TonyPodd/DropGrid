"""Safe local preparation summary; no credentials, captions or external payloads."""

from collections import Counter
from datetime import timedelta
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
from dropgrid.services.account_pools import distribute, pool
from dropgrid.services.catalog import get_entity
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
    )
    job = await session.scalar(
        select(CampaignPreparationJob).where(CampaignPreparationJob.campaign_id == campaign_id)
    )
    duration = (
        ((job.updated_at if job.state == "ready" else utcnow()) - job.created_at).total_seconds()
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
        "write_attempts": sum(r.attempt_count for r, _ in rows),
        "submitted": sum(r.submitted_at is not None for r, _ in rows),
    }
