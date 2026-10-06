import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import Campaign, Community, Grid, GridCommunity, Submission
from dropgrid.domain.enums import CampaignStatus, SubmissionStatus
from dropgrid.services.campaigns import prepare_campaign
from dropgrid.services.catalog import ConflictError

pytestmark = pytest.mark.integration


async def test_prepare_concurrent_and_history(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as db, db.begin():
        grid = Grid(name="Grid")
        communities = [Community(domain=f"test{i}") for i in range(3)]
        db.add_all([grid, *communities])
        await db.flush()
        db.add_all([GridCommunity(grid_id=grid.id, community_id=c.id) for c in communities])
        campaign = Campaign(name="Campaign", grid_id=grid.id, track_url="https://vk.com/audio1_2")
        db.add(campaign)
        await db.flush()
        campaign_id = campaign.id

    async def prepare() -> int:
        async with sessions() as db, db.begin():
            return (await prepare_campaign(db, campaign_id)).created

    assert sorted(await asyncio.gather(prepare(), prepare())) == [0, 3]
    async with sessions() as db, db.begin():
        assert await db.scalar(select(func.count()).select_from(Submission)) == 3
        submission = await db.scalar(select(Submission).limit(1))
        assert submission is not None
        submission.status = SubmissionStatus.failed
        submission.attempt_count = 2
    assert await prepare() == 0
    async with sessions() as db, db.begin():
        failed = await db.scalar(
            select(Submission).where(Submission.status == SubmissionStatus.failed)
        )
        assert failed is not None and failed.attempt_count == 2
        campaign = await db.get(Campaign, campaign_id)
        assert campaign is not None
        campaign.status = CampaignStatus.completed
    with pytest.raises(ConflictError):
        await prepare()
