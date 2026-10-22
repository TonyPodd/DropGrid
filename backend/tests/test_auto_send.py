from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_campaign_sender import USER, seed
from test_photo_integration import prepared

from dropgrid.config import Settings
from dropgrid.db.models import Account, Campaign, CampaignPreparationJob, Submission, utcnow
from dropgrid.domain.enums import CampaignStatus, SubmissionStatus
from dropgrid.photos.campaign_preparation import enqueue
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.services.account_pools import accounts_needed
from dropgrid.services.sending import AUTOSTART_RECHECK, autostart


@pytest.mark.parametrize(
    "communities,needed", [(0, 0), (1, 1), (100, 1), (101, 2), (514, 6), (521, 6), (600, 6)]
)
def test_one_account_per_hundred_communities(communities, needed):
    assert accounts_needed(communities, 100) == needed


def engine():
    return SimpleNamespace(planner=SimpleNamespace(settings=Settings(_env_file=None)))


async def accounts(sessions, count):
    async with sessions() as db, db.begin():
        rows = [
            Account(name=f"A{i}", vk_user_id=USER + i, encrypted_access_token="mock-only")
            for i in range(count)
        ]
        db.add_all(rows)
        await db.flush()
        return [a.id for a in rows]


@pytest.mark.integration
async def test_suggesting_posts_needs_an_account_per_hundred_publics(sessions):
    cid = await prepared(sessions, count=101)
    one, two = await accounts(sessions, 2)
    async with sessions() as db, db.begin():
        with pytest.raises(PhotoConflict) as error:
            await enqueue(db, cid, engine(), [one], auto_send=True)
    assert error.value.code == "accounts_needed"
    async with sessions() as db, db.begin():
        # Only selecting photos has no such requirement.
        await enqueue(db, cid, engine(), [one], auto_send=False)
    async with sessions() as db:
        assert (await db.get(Campaign, cid)).auto_send is False
    async with sessions() as db, db.begin():
        (await db.scalar(select(CampaignPreparationJob))).state = "ready"
    async with sessions() as db, db.begin():
        await enqueue(db, cid, engine(), [one, two], auto_send=True)
    async with sessions() as db, db.begin():
        (await db.scalar(select(CampaignPreparationJob))).state = "ready"
    async with sessions() as db, db.begin():
        # A retry of failed photos keeps the operator's choice.
        await enqueue(db, cid, engine(), None, [(await db.scalar(select(Submission.id)))])
    async with sessions() as db:
        assert (await db.get(Campaign, cid)).auto_send is True


async def campaign_state(sessions, cid):
    async with sessions() as db:
        return await db.get(Campaign, cid)


@pytest.mark.integration
async def test_autostart_suggests_posts_once_photos_are_ready(sessions, tmp_path):
    cid, _, _, ids, storage = await seed(sessions, tmp_path, count=2)
    settings = Settings(_env_file=None)
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        campaign.preparation_state = "ready"
    # «Только подобрать фото»: nothing is sent.
    assert await autostart(sessions, storage, settings, {}) == 0
    assert (await campaign_state(sessions, cid)).status == CampaignStatus.ready
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        campaign.auto_send, campaign.preparation_state = True, "awaiting_review"
    # Photos still under review: wait.
    assert await autostart(sessions, storage, settings, {}) == 0
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        campaign.preparation_state, campaign.track_audio_id = "ready", None
    checked = {}
    # Preflight is not ready (no audio): the campaign waits and is rechecked rarely.
    assert await autostart(sessions, storage, settings, checked) == 0
    assert (await campaign_state(sessions, cid)).status == CampaignStatus.ready
    async with sessions() as db, db.begin():
        (await db.get(Campaign, cid)).track_audio_id = 456245636
    assert await autostart(sessions, storage, settings, checked) == 0
    checked[cid] = utcnow() - AUTOSTART_RECHECK - timedelta(seconds=1)
    assert await autostart(sessions, storage, settings, checked) == 1
    assert (await campaign_state(sessions, cid)).status == CampaignStatus.running
    async with sessions() as db:
        rows = (await db.scalars(select(Submission).where(Submission.id.in_(ids)))).all()
    assert {r.vk_send_phase for r in rows} == {"queued"}
    assert {r.status for r in rows} == {SubmissionStatus.pending}
    # Already running: never started twice.
    assert await autostart(sessions, storage, settings, {}) == 0


@pytest.mark.integration
async def test_autostart_skips_publics_without_photo(sessions, tmp_path):
    cid, _, _, ids, storage = await seed(sessions, tmp_path, count=2)
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        campaign.auto_send, campaign.preparation_state = True, "ready"
        (await db.get(Submission, ids[0])).media_asset_id = None
    assert await autostart(sessions, storage, Settings(_env_file=None), {}) == 1
    async with sessions() as db:
        skipped, queued = await db.get(Submission, ids[0]), await db.get(Submission, ids[1])
    assert skipped.status == SubmissionStatus.skipped and skipped.error_code == "media_missing"
    assert queued.vk_send_phase == "queued"


@pytest.mark.integration
async def test_dry_run_cannot_suggest_posts(sessions):
    cid = await prepared(sessions, count=1)
    (one,) = await accounts(sessions, 1)
    async with sessions() as db, db.begin():
        (await db.get(Campaign, cid)).is_dry_run = True
    async with sessions() as db, db.begin():
        with pytest.raises(Exception, match="Dry-run campaigns cannot be sent"):
            await enqueue(db, cid, engine(), [one], auto_send=True)
