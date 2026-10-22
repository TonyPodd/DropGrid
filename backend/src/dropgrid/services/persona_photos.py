"""Photos of the girl behind an account, for its dating/selfie publics.

Each account (one VK token) has any number of selfies and other photos, at least one of
each before sending. A photo belongs to one account only, so it can never be sent with
another token. No VK calls.
"""

import asyncio
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.api.schemas import (
    CampaignPersonaRead,
    PersonaAccountRead,
    PersonaCategoryRead,
    PersonaPhotoKind,
    PersonaPhotoPatch,
    PersonaPhotoRead,
)
from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    AccountPersonaPhoto,
    Campaign,
    CampaignAccount,
    GridCommunity,
    MediaAsset,
    Submission,
)
from dropgrid.domain.personas import (
    MAX_PHOTOS_PER_ACCOUNT,
    PERSONA_PROVIDER,
    is_persona_category,
)
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.photos.domain import PhotoError, PhotoPolicy
from dropgrid.photos.images import MediaStorage, normalize_image
from dropgrid.services.catalog import InvalidGridError, NotFoundError, get_entity

MAX_UPLOAD_BYTES = PhotoPolicy().max_input_bytes
SELFIE_TAG = "селфи"


def _read(link: AccountPersonaPhoto, asset: MediaAsset) -> PersonaPhotoRead:
    return PersonaPhotoRead(
        kind="selfie" if link.kind == "selfie" else "other",
        media_asset_id=asset.id,
        tags=list(asset.tags),
        width=asset.width,
        height=asset.height,
        created_at=link.created_at,
    )


def _tags(kind: str, tags: list[str]) -> list[str]:
    # Selfies always say so (it drives the public-name text signal); others never do.
    rest = [tag for tag in tags if tag != SELFIE_TAG]
    return [SELFIE_TAG, *rest] if kind == "selfie" else rest


async def list_photos(session: AsyncSession, account_id: UUID) -> list[PersonaPhotoRead]:
    await get_entity(session, Account, account_id)
    rows = await session.execute(
        select(AccountPersonaPhoto, MediaAsset)
        .join(MediaAsset, MediaAsset.id == AccountPersonaPhoto.media_asset_id)
        .where(AccountPersonaPhoto.account_id == account_id)
        .order_by(AccountPersonaPhoto.kind.desc(), AccountPersonaPhoto.created_at, MediaAsset.id)
    )
    return [_read(link, asset) for link, asset in rows.tuples()]


async def add_photo(
    session: AsyncSession,
    account_id: UUID,
    kind: PersonaPhotoKind,
    data: bytes,
    storage: MediaStorage,
    tags: list[str],
) -> PersonaPhotoRead:
    """Add one more selfie or other photo of the girl behind this account."""
    # The account row lock serializes concurrent uploads and the per-account limit.
    if await session.get(Account, account_id, with_for_update=True) is None:
        raise NotFoundError("Account not found")
    count = await session.scalar(
        select(func.count()).where(AccountPersonaPhoto.account_id == account_id)
    )
    if (count or 0) >= MAX_PHOTOS_PER_ACCOUNT:
        raise PhotoConflict("persona_photo_limit", "Too many photos for one account")
    try:
        image = await asyncio.to_thread(normalize_image, data, PhotoPolicy())
    except PhotoError as error:
        raise InvalidGridError(f"Photo rejected: {error.code}") from None
    asset = await session.scalar(select(MediaAsset).where(MediaAsset.sha256 == image.sha256))
    if asset is not None:
        if asset.provider != PERSONA_PROVIDER:
            raise PhotoConflict("persona_photo_in_library", "Photo is already a library photo")
        owner = await session.scalar(
            select(AccountPersonaPhoto.account_id).where(
                AccountPersonaPhoto.media_asset_id == asset.id
            )
        )
        if owner == account_id:
            raise PhotoConflict("persona_photo_duplicate", "Photo is already uploaded")
        # Ownership also follows history: a photo once sent by another girl stays hers.
        sent_by_other = await session.scalar(
            select(Submission.id)
            .where(
                Submission.media_asset_id == asset.id,
                Submission.account_id.is_not(None),
                Submission.account_id != account_id,
            )
            .limit(1)
        )
        if owner is not None or sent_by_other:
            raise PhotoConflict("persona_photo_taken", "Photo belongs to another account")
    else:
        key = await asyncio.to_thread(storage.write, image)
        asset = MediaAsset(
            storage_key=key,
            provider=PERSONA_PROVIDER,
            provider_asset_id=image.sha256,
            license_code="owner_upload",
            license_name="Uploaded by the account owner",
            license_url="",
            width=image.width,
            height=image.height,
            mime_type="image/jpeg",
            byte_size=len(image.data),
            sha256=image.sha256,
            perceptual_hash=image.perceptual_hash,
        )
        session.add(asset)
        await session.flush()
    asset.tags, asset.enabled = _tags(kind, tags), True
    link = AccountPersonaPhoto(account_id=account_id, media_asset_id=asset.id, kind=kind)
    session.add(link)
    await session.flush()
    return _read(link, asset)


async def _link(
    session: AsyncSession, account_id: UUID, asset_id: UUID
) -> tuple[AccountPersonaPhoto, MediaAsset]:
    link = await session.get(AccountPersonaPhoto, (account_id, asset_id), with_for_update=True)
    asset = await session.get(MediaAsset, asset_id)
    if link is None or asset is None:
        raise NotFoundError("Photo is not linked to this account")
    return link, asset


async def patch_photo(
    session: AsyncSession, account_id: UUID, asset_id: UUID, data: PersonaPhotoPatch
) -> PersonaPhotoRead:
    """Move a photo between selfie/other or change what it shows."""
    link, asset = await _link(session, account_id, asset_id)
    if data.kind is not None:
        link.kind = data.kind
    tags = [t.strip().casefold() for t in data.tags if t.strip()] if data.tags is not None else None
    asset.tags = _tags(link.kind, list(asset.tags) if tags is None else tags)
    await session.flush()
    return _read(link, asset)


async def remove_photo(session: AsyncSession, account_id: UUID, asset_id: UUID) -> None:
    # The MediaAsset stays: submissions and review history may still reference it.
    link, _ = await _link(session, account_id, asset_id)
    await session.delete(link)
    await session.flush()


async def campaign_persona(
    session: AsyncSession, campaign_id: UUID, settings: Settings
) -> CampaignPersonaRead:
    campaign = await get_entity(session, Campaign, campaign_id)
    counts = await session.execute(
        select(GridCommunity.category, func.count())
        .where(GridCommunity.grid_id == campaign.grid_id)
        .group_by(GridCommunity.category)
        .order_by(GridCommunity.category)
    )
    categories = [
        PersonaCategoryRead(category=category, count=count)
        for category, count in counts.tuples()
        if category is not None and is_persona_category(category)
    ]
    selfies, others = (
        select(func.count())
        .where(AccountPersonaPhoto.account_id == Account.id, AccountPersonaPhoto.kind == kind)
        .scalar_subquery()
        for kind in ("selfie", "other")
    )
    girls = await session.execute(
        select(Account, CampaignAccount.max_submissions, selfies, others)
        .join(CampaignAccount, CampaignAccount.account_id == Account.id)
        .where(
            CampaignAccount.campaign_id == campaign_id,
            CampaignAccount.enabled.is_(True),
            CampaignAccount.persona.is_(True),
        )
        .order_by(CampaignAccount.priority, Account.id)
    )
    return CampaignPersonaRead(
        categories=categories,
        targets=sum(c.count for c in categories),
        default_quota=settings.account_campaign_send_quota,
        accounts=[
            PersonaAccountRead(
                account_id=a.id, name=a.name, quota=quota, selfies=selfie, others=other
            )
            for a, quota, selfie, other in girls.tuples()
        ],
    )
