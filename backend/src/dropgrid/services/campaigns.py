from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.api.schemas import CampaignCreate, CampaignPatch, PrepareRead
from dropgrid.db.models import Campaign, Grid, GridCommunity, Submission
from dropgrid.domain.enums import CampaignStatus
from dropgrid.services.catalog import ConflictError, NotFoundError, get_entity


async def create_campaign(session: AsyncSession, data: CampaignCreate) -> Campaign:
    await get_entity(session, Grid, data.grid_id)
    campaign = Campaign(**data.model_dump())
    session.add(campaign)
    await session.flush()
    return campaign


async def locked_campaign(session: AsyncSession, campaign_id: UUID) -> Campaign:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if campaign is None:
        raise NotFoundError("Campaign not found")
    return campaign


async def patch_campaign(session: AsyncSession, campaign_id: UUID, data: CampaignPatch) -> Campaign:
    campaign = await locked_campaign(session, campaign_id)
    if campaign.status != CampaignStatus.draft:
        raise ConflictError("Only draft campaigns can be edited")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(campaign, key, value)
    await session.flush()
    return campaign


async def prepare_campaign(session: AsyncSession, campaign_id: UUID) -> PrepareRead:
    campaign = await locked_campaign(session, campaign_id)
    if campaign.status not in {CampaignStatus.draft, CampaignStatus.ready}:
        raise ConflictError("Campaign cannot be prepared in its current status")
    community_ids = (
        await session.scalars(
            select(GridCommunity.community_id).where(GridCommunity.grid_id == campaign.grid_id)
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
