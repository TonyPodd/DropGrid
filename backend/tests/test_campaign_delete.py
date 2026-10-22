import pytest
from sqlalchemy import select
from test_campaign_sender import seed
from test_photo_integration import prepared

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Campaign,
    CampaignPreparationJob,
    MediaAsset,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
)
from dropgrid.domain.enums import CampaignStatus
from dropgrid.photos.history import proposed_photos
from dropgrid.services.sending import autostart


async def status(sessions, cid, value=None, **changes):
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        if value:
            campaign.status = value
        for key, item in changes.items():
            setattr(campaign, key, item)


@pytest.mark.integration
@pytest.mark.parametrize(
    "state", [CampaignStatus.draft, CampaignStatus.ready, CampaignStatus.completed]
)
async def test_delete_hides_a_campaign_everywhere(client, sessions, state):
    cid = await prepared(sessions, count=1)
    await status(sessions, cid, state)
    assert (await client.delete(f"/api/v1/campaigns/{cid}")).status_code == 204
    assert (await client.get(f"/api/v1/campaigns/{cid}")).status_code == 404
    assert all(c["id"] != str(cid) for c in (await client.get("/api/v1/campaigns")).json())
    assert (await client.get(f"/api/v1/campaigns/{cid}/stats")).status_code == 404
    assert (await client.delete(f"/api/v1/campaigns/{cid}")).status_code == 404
    dashboard = (await client.get("/api/v1/dashboard")).json()
    assert dashboard["counts"]["campaigns"] == 0
    async with sessions() as db:
        # Rows stay for photo history; only the product hides the campaign.
        assert (await db.get(Campaign, cid)).deleted_at is not None
        assert await db.scalar(select(Submission).where(Submission.campaign_id == cid))


@pytest.mark.integration
@pytest.mark.parametrize("state", [CampaignStatus.running, CampaignStatus.monitoring])
async def test_a_campaign_in_progress_is_stopped_before_deletion(client, sessions, state):
    cid = await prepared(sessions, count=1)
    await status(sessions, cid, state)
    refused = await client.delete(f"/api/v1/campaigns/{cid}")
    assert refused.status_code == 409
    assert refused.json() == {"detail": {"code": "campaign_in_progress"}}
    assert (await client.post(f"/api/v1/campaigns/{cid}/cancel")).json()["status"] == "cancelled"
    assert (await client.delete(f"/api/v1/campaigns/{cid}")).status_code == 204


@pytest.mark.integration
async def test_a_preparing_campaign_is_stopped_before_deletion(client, sessions):
    cid = await prepared(sessions, count=1)
    async with sessions() as db, db.begin():
        account = Account(name="A", vk_user_id=1, encrypted_access_token="mock-only")
        db.add(account)
        await db.flush()
        db.add(CampaignPreparationJob(campaign_id=cid, account_id=account.id, state="running"))
    refused = await client.delete(f"/api/v1/campaigns/{cid}")
    assert refused.json() == {"detail": {"code": "campaign_in_progress"}}
    assert (await client.post(f"/api/v1/campaigns/{cid}/cancel")).status_code == 200
    async with sessions() as db, db.begin():
        # The preparation worker notices the cancellation and stops the job.
        (await db.scalar(select(CampaignPreparationJob))).state = "cancelled"
    assert (await client.delete(f"/api/v1/campaigns/{cid}")).status_code == 204


@pytest.mark.integration
async def test_deleted_campaign_photos_still_never_repeat(client, sessions):
    cid = await prepared(sessions, count=1)
    async with sessions() as db, db.begin():
        row = await db.scalar(select(Submission).where(Submission.campaign_id == cid))
        photo = MediaAsset(
            storage_key="x.jpg",
            sha256="b" * 64,
            provider="pinterest",
            provider_asset_id="pin-9",
            width=800,
            height=800,
            mime_type="image/jpeg",
        )
        db.add(photo)
        await db.flush()
        context = PhotoSelectionSession(
            campaign_id=cid,
            submission_id=row.id,
            community_id=row.community_id,
            baseline_rank=1,
            proposed_rank=1,
        )
        db.add(context)
        await db.flush()
        db.add(
            PhotoSelectionCandidate(
                selection_session_id=context.id,
                rank=1,
                provider="pinterest",
                source_identity="pin-9",
                media_asset_id=photo.id,
                features={},
                candidate={"provider": "pinterest", "provider_asset_id": "pin-9"},
            )
        )
        other = Campaign(
            name="Next", grid_id=(await db.get(Campaign, cid)).grid_id, track_url="https://vk.ru/a"
        )
        db.add(other)
        await db.flush()
        other_id = other.id
    assert (await client.delete(f"/api/v1/campaigns/{cid}")).status_code == 204
    async with sessions() as db:
        seen = await proposed_photos(db, other_id)
    assert ("pinterest", "pin-9") in seen.identities


@pytest.mark.integration
async def test_autostart_ignores_deleted_campaigns(client, sessions, tmp_path):
    cid, _, _, _, storage = await seed(sessions, tmp_path)
    await status(sessions, cid, auto_send=True, preparation_state="ready")
    assert (await client.delete(f"/api/v1/campaigns/{cid}")).status_code == 204
    assert await autostart(sessions, storage, Settings(_env_file=None), {}) == 0
    async with sessions() as db:
        assert (await db.get(Campaign, cid)).status == CampaignStatus.ready
