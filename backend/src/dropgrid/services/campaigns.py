from uuid import UUID

from sqlalchemy import func, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.api.schemas import CampaignCreate, CampaignPatch, PrepareRead
from dropgrid.db.models import (
    Campaign,
    CampaignPreparationJob,
    Grid,
    GridCommunity,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import CampaignStatus
from dropgrid.integrations.vk.helpers import parse_vk_audio_reference
from dropgrid.services.catalog import ConflictError, NotFoundError, get_entity


async def create_campaign(session: AsyncSession, data: CampaignCreate) -> Campaign:
    await get_entity(session, Grid, data.grid_id)
    values = data.model_dump()
    audio = parse_vk_audio_reference(data.track_url)
    values.update(track_owner_id=audio.owner_id, track_audio_id=audio.media_id)
    campaign = Campaign(**values)
    session.add(campaign)
    await session.flush()
    return campaign


async def locked_campaign(session: AsyncSession, campaign_id: UUID) -> Campaign:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if campaign is None or campaign.deleted_at is not None:
        raise NotFoundError("Campaign not found")
    return campaign


async def delete_campaign(session: AsyncSession, campaign_id: UUID) -> None:
    """Delete a draft or a finished campaign; one in progress must be stopped first."""
    from dropgrid.photos.conflicts import PhotoConflict

    campaign = await locked_campaign(session, campaign_id)
    preparing = await session.scalar(
        select(CampaignPreparationJob.id).where(
            CampaignPreparationJob.campaign_id == campaign_id,
            CampaignPreparationJob.state.in_(("queued", "running")),
        )
    )
    if preparing or campaign.status in {CampaignStatus.running, CampaignStatus.monitoring}:
        raise PhotoConflict("campaign_in_progress", "Stop the campaign before deleting it")
    campaign.deleted_at = utcnow()
    await session.flush()


async def patch_campaign(session: AsyncSession, campaign_id: UUID, data: CampaignPatch) -> Campaign:
    campaign = await locked_campaign(session, campaign_id)
    if campaign.status != CampaignStatus.draft:
        raise ConflictError("Only draft campaigns can be edited")
    values = data.model_dump(exclude_unset=True)
    # IDs are derived, never independently mutable or inconsistent with the link.
    if "track_url" in values or "track_owner_id" in values or "track_audio_id" in values:
        audio = parse_vk_audio_reference(data.track_url or campaign.track_url)
        values.update(track_owner_id=audio.owner_id, track_audio_id=audio.media_id)
    for key, value in values.items():
        setattr(campaign, key, value)
    await session.flush()
    return campaign


async def prepare_campaign(session: AsyncSession, campaign_id: UUID) -> PrepareRead:
    campaign = await locked_campaign(session, campaign_id)
    if campaign.status not in {CampaignStatus.draft, CampaignStatus.ready}:
        raise ConflictError("Campaign cannot be prepared in its current status")
    community_ids = (
        await session.scalars(
            select(GridCommunity.community_id).where(
                GridCommunity.grid_id == campaign.grid_id,
                GridCommunity.community_id.in_(campaign.dry_run_scope)
                if campaign.dry_run_scope is not None
                else true(),
            )
        )
    ).all()
    created = 0
    if community_ids:
        rows = [{"campaign_id": campaign_id, "community_id": cid} for cid in community_ids]
        result = await session.scalars(
            insert(Submission)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_submission_campaign_community")
            .returning(Submission.id)
        )
        created = len(result.all())
    campaign.status = CampaignStatus.ready
    await session.flush()
    total = await session.scalar(
        select(func.count()).select_from(Submission).where(Submission.campaign_id == campaign_id)
    )
    return PrepareRead(campaign_id=campaign_id, created=created, total=total or 0)
