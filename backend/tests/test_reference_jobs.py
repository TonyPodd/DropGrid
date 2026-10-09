from datetime import timedelta
from types import SimpleNamespace

import pytest

from dropgrid.db.models import Community, CommunityContentProfile, utcnow
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.photos.reference_jobs import ReferenceJobs
from dropgrid.photos.reference_schemas import ReferenceSyncInput, ReferenceSyncRead

pytestmark = pytest.mark.integration


async def test_durable_job_progress_conflict_and_completion(sessions):
    async with sessions() as s, s.begin():
        row = Community(domain="accordclubrus", vk_group_id=123)
        s.add(row)
        await s.flush()
        cid = row.id
    states = []
    service = None

    async def sync(community_id, account_id, target, *, progress):
        assert community_id == cid and target == 12
        report = ReferenceSyncRead(posts_scanned=42, photo_posts_found=12)
        for state in ("reading_wall", "downloading", "embedding", "finalizing"):
            await progress(state, report)
            current = await service.latest(cid)
            assert current["state"] == state and current["progress"]["posts_scanned"] == 42
            states.append(state)
        return report

    service = ReferenceJobs(SimpleNamespace(sessions=sessions, sync=sync))
    queued = await service.enqueue(cid, ReferenceSyncInput(target_count=12))
    assert queued["state"] == "queued"
    with pytest.raises(PhotoConflict, match="Photo operation") as error:
        await service.enqueue(cid, ReferenceSyncInput())
    assert error.value.code == "reference_sync_in_progress"
    assert await service.tick() == 1
    assert (await service.latest(cid))["state"] == "ready"
    assert len(states) == 4 and await service.tick() == 0


async def test_failed_job_never_exposes_exception(sessions):
    async with sessions() as s, s.begin():
        row = Community(domain="bmw")
        s.add(row)
        await s.flush()
        cid = row.id

    async def fail(*args, **kwargs):
        raise RuntimeError("sensitive vendor response")

    jobs = ReferenceJobs(SimpleNamespace(sessions=sessions, sync=fail))
    await jobs.enqueue(cid, ReferenceSyncInput())
    await jobs.tick()
    result = await jobs.latest(cid)
    assert result["state"] == "failed"
    assert "sensitive" not in str(result) and result["error_code"] == "reference_sync_failed"


async def test_profile_conflict_safe_api(client, sessions):
    async with sessions() as s, s.begin():
        row = Community(domain="locked")
        s.add(row)
        await s.flush()
        cid = row.id
        s.add(
            CommunityContentProfile(
                community_id=cid, sync_lease_until=utcnow() + timedelta(minutes=2)
            )
        )
    response = await client.put(f"/api/v1/communities/{cid}/content-profile", json={})
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "profile_locked"}}
