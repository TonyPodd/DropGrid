"""Photos the bot has already proposed, so a new preparation looks for fresh ones."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import (
    CommunityReferencePhoto,
    MediaAsset,
    PhotoPreviewCache,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
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

    def remember_item(self, item: PoolCandidate) -> None:
        self.remember(
            item.identity, item.asset.id if item.asset else None, item.sha256, item.perceptual_hash
        )
        if item.asset:
            self.remember_asset(item.asset)

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


async def proposed_photos(session: AsyncSession, campaign_id: UUID) -> ProposedPhotos:
    """Every photo the bot proposed or an operator chose before this planning run.

    Earlier selection sessions of the same campaign count too: preparing again
    must bring new photos. Photos still assigned in this campaign are governed
    by the per-campaign reuse limit instead.
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
            or_(
                PhotoSelectionCandidate.rank == PhotoSelectionSession.proposed_rank,
                PhotoSelectionCandidate.selected.is_(True),
            )
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
        .where(Submission.campaign_id != campaign_id)
        .distinct()
    )
    for asset in assets:
        seen.remember_asset(asset)
    return seen
