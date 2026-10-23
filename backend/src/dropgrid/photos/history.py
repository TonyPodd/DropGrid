"""Photos used in other campaigns, so a new campaign looks for fresh ones.

Within one campaign a photo may repeat across communities. A photo whose last use in
another campaign is older than PHOTO_REUSE_AFTER_DAYS (3 months) is available again.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.config import Settings
from dropgrid.db.models import (
    CommunityReferencePhoto,
    MediaAsset,
    PhotoPreviewCache,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
    utcnow,
)
from dropgrid.photos.domain import PhotoCandidate
from dropgrid.photos.pool import PoolCandidate


@dataclass
class ProposedPhotos:
    identities: set[tuple[str, str]] = field(default_factory=set)
    asset_ids: set[UUID] = field(default_factory=set)
    sha256: set[str] = field(default_factory=set)
    perceptual_hashes: set[str] = field(default_factory=set)

    def remember(
        self,
        identity: tuple[str, str] | None = None,
        asset_id: UUID | None = None,
        sha256: str | None = None,
        perceptual_hash: str | None = None,
    ) -> None:
        # Library assets without a provider share an empty identity; never match on it.
        if identity and all(identity):
            self.identities.add(identity)
        if asset_id:
            self.asset_ids.add(asset_id)
        if sha256:
            self.sha256.add(sha256)
        if perceptual_hash:
            self.perceptual_hashes.add(perceptual_hash)

    def remember_asset(self, asset: MediaAsset) -> None:
        self.remember(
            (asset.provider or "", asset.provider_asset_id or ""),
            asset.id,
            asset.sha256,
            asset.perceptual_hash,
        )

    def photo(self, candidate: PhotoCandidate) -> bool:
        return (candidate.provider, candidate.provider_asset_id) in self.identities

    def asset(self, asset: MediaAsset) -> bool:
        return (
            asset.id in self.asset_ids
            or (asset.provider or "", asset.provider_asset_id or "") in self.identities
            or bool(asset.sha256 and asset.sha256 in self.sha256)
            or bool(asset.perceptual_hash and asset.perceptual_hash in self.perceptual_hashes)
        )

    def __contains__(self, item: PoolCandidate) -> bool:
        return (
            self.photo(item.photo)
            or (item.asset is not None and self.asset(item.asset))
            or bool(item.sha256 and item.sha256 in self.sha256)
            or bool(item.perceptual_hash and item.perceptual_hash in self.perceptual_hashes)
        )


def reuse_since(settings: Settings) -> datetime:
    """Photos last used before this moment may be proposed again (3 months by default)."""
    return utcnow() - timedelta(days=settings.photo_reuse_after_days)


async def proposed_photos(
    session: AsyncSession, campaign_id: UUID, since: datetime | None = None
) -> ProposedPhotos:
    """Every photo the bot proposed, an operator chose or a submission got in OTHER campaigns.

    The campaign's own photos are governed by the per-campaign reuse limit instead.
    """
    seen = ProposedPhotos()
    rows = await session.execute(
        select(
            PhotoSelectionCandidate.candidate,
            PhotoSelectionCandidate.media_asset_id,
            MediaAsset.sha256,
            MediaAsset.perceptual_hash,
            PhotoPreviewCache.sha256,
            PhotoPreviewCache.perceptual_hash,
            CommunityReferencePhoto.sha256,
            CommunityReferencePhoto.perceptual_hash,
        )
        .join(
            PhotoSelectionSession,
            PhotoSelectionSession.id == PhotoSelectionCandidate.selection_session_id,
        )
        .outerjoin(MediaAsset, MediaAsset.id == PhotoSelectionCandidate.media_asset_id)
        .outerjoin(PhotoPreviewCache, PhotoPreviewCache.id == PhotoSelectionCandidate.preview_id)
        .outerjoin(
            CommunityReferencePhoto,
            CommunityReferencePhoto.id == PhotoSelectionCandidate.reference_id,
        )
        .where(
            PhotoSelectionSession.campaign_id != campaign_id,
            or_(
                PhotoSelectionCandidate.rank == PhotoSelectionSession.proposed_rank,
                PhotoSelectionCandidate.selected.is_(True),
            ),
            func.coalesce(PhotoSelectionSession.confirmed_at, PhotoSelectionSession.created_at)
            >= since
            if since
            else true(),
        )
    )
    for candidate, asset_id, *hashes in rows.tuples():
        provider, identity = candidate.get("provider"), candidate.get("provider_asset_id")
        seen.remember(
            (provider, identity)
            if isinstance(provider, str) and isinstance(identity, str)
            else None,
            asset_id,
        )
        for sha256, perceptual_hash in zip(hashes[::2], hashes[1::2], strict=True):
            seen.remember(sha256=sha256, perceptual_hash=perceptual_hash)
    assets = await session.scalars(
        select(MediaAsset)
        .join(Submission, Submission.media_asset_id == MediaAsset.id)
        .where(
            Submission.campaign_id != campaign_id,
            func.coalesce(Submission.submitted_at, Submission.created_at) >= since
            if since
            else true(),
        )
        .distinct()
    )
    for asset in assets:
        seen.remember_asset(asset)
    return seen


async def used_assets(session: AsyncSession, asset_ids: list[UUID], since: datetime) -> set[UUID]:
    """Which of these assets some campaign used since `since` (not available right now)."""
    if not asset_ids:
        return set()
    proposed = await session.scalars(
        select(PhotoSelectionCandidate.media_asset_id)
        .join(
            PhotoSelectionSession,
            PhotoSelectionSession.id == PhotoSelectionCandidate.selection_session_id,
        )
        .where(
            PhotoSelectionCandidate.media_asset_id.in_(asset_ids),
            or_(
                PhotoSelectionCandidate.rank == PhotoSelectionSession.proposed_rank,
                PhotoSelectionCandidate.selected.is_(True),
            ),
            func.coalesce(PhotoSelectionSession.confirmed_at, PhotoSelectionSession.created_at)
            >= since,
        )
    )
    assigned = await session.scalars(
        select(Submission.media_asset_id).where(
            Submission.media_asset_id.in_(asset_ids),
            func.coalesce(Submission.submitted_at, Submission.created_at) >= since,
        )
    )
    return {a for a in [*proposed, *assigned] if a is not None}
