"""Community usage tracks verified DropGrid receipts, never archive wall age."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import CommunityMediaUsage, MediaAsset
from dropgrid.photos.domain import Deduplicator

COMMUNITY_COOLDOWN_DAYS = 180
ARCHIVE_AGE_PENALTY_MAX = 0.03
ARCHIVE_AGE_PENALTY_END_DAYS = 365


def archive_age_penalty(posted_at: datetime | None, now: datetime, min_age_days: int) -> float:
    if posted_at is None:
        return 0
    age = (now - posted_at).total_seconds() / 86400
    end = max(ARCHIVE_AGE_PENALTY_END_DAYS, min_age_days)
    if age >= end:
        return 0
    return ARCHIVE_AGE_PENALTY_MAX * max(0, min(1, (end - age) / max(1, end - min_age_days)))


def recently_used(
    rows: list[CommunityMediaUsage],
    provider: str,
    identity: str,
    sha256: str | None,
    perceptual_hash: str | None,
    now: datetime,
    media_asset_id: UUID | None = None,
) -> bool:
    duplicate = Deduplicator()
    boundary = now - timedelta(days=COMMUNITY_COOLDOWN_DAYS)
    return any(
        r.last_used_at > boundary
        and (
            (r.source_provider == provider and r.source_identity == identity)
            or (media_asset_id is not None and r.media_asset_id == media_asset_id)
            or (bool(sha256) and r.sha256 == sha256)
            or duplicate.near(r.perceptual_hash, perceptual_hash)
        )
        for r in rows
    )


async def record_media_usage(
    session: AsyncSession,
    community_id: UUID,
    asset: MediaAsset,
    submission_id: UUID,
    used_at: datetime,
) -> None:
    provider, identity = asset.provider or "library", asset.provider_asset_id or str(asset.id)
    stmt = insert(CommunityMediaUsage).values(
        community_id=community_id,
        media_asset_id=asset.id,
        source_provider=provider,
        source_identity=identity,
        sha256=asset.sha256,
        perceptual_hash=asset.perceptual_hash,
        first_used_at=used_at,
        last_used_at=used_at,
        use_count=1,
        last_submission_id=submission_id,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["community_id", "source_provider", "source_identity"],
            set_={
                "last_used_at": used_at,
                "use_count": CommunityMediaUsage.use_count + 1,
                "last_submission_id": submission_id,
                "media_asset_id": asset.id,
                "sha256": asset.sha256,
                "perceptual_hash": asset.perceptual_hash,
            },
            where=CommunityMediaUsage.last_submission_id.is_distinct_from(submission_id),
        )
    )


async def community_usage(
    session: AsyncSession, community_id: UUID, now: datetime
) -> list[CommunityMediaUsage]:
    return list(
        (
            await session.scalars(
                select(CommunityMediaUsage).where(
                    CommunityMediaUsage.community_id == community_id,
                    CommunityMediaUsage.last_used_at
                    > now - timedelta(days=COMMUNITY_COOLDOWN_DAYS),
                )
            )
        ).all()
    )
