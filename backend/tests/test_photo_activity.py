from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from dropgrid.api.activity import list_activity
from dropgrid.db.models import Community, PhotoOperationJob, ReferenceSyncJob, utcnow
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.photos.operation_jobs import OperationInput, PhotoJobs
from dropgrid.photos.progress import emit
from dropgrid.photos.reference_jobs import ReferenceJobs
from dropgrid.photos.reference_schemas import PhotoPreviewRead, ReferenceSyncInput

pytestmark = pytest.mark.integration


async def test_preview_durable_progress_conflict_result_and_activity(sessions, monkeypatch):
    async with sessions() as session, session.begin():
        row = Community(domain="accord", name="Honda Accord")
        session.add(row)
        await session.flush()
        cid = row.id
    engine = SimpleNamespace(planner=None, visual=None)
    jobs = PhotoJobs(engine, SimpleNamespace(sessions=sessions))

    async def preview(*args):
        await emit("pinterest_search", 2, 4)
        current = await jobs.latest(cid, "preview")
        assert current["state"] == "running" and current["current"] == 2
        async with sessions() as session:
            activities = await list_activity(session)
            assert activities[0].percent == 50
            assert activities[0].state == "running"
        return PhotoPreviewRead(
            community_id=cid,
            category="Honda",
            references=[],
            category_only=[],
            community_aware=[],
            warnings=["pinterest_fallback_used"],
        )

    monkeypatch.setattr("dropgrid.photos.operation_jobs.photo_preview", preview)
    queued = await jobs.enqueue(cid, OperationInput(kind="preview"))
    assert queued["state"] == "queued"
    with pytest.raises(PhotoConflict):
        await jobs.enqueue(cid, OperationInput(kind="archive"))
    with pytest.raises(PhotoConflict):
        await ReferenceJobs(SimpleNamespace(sessions=sessions)).enqueue(cid, ReferenceSyncInput())
    assert await jobs.tick() == 1 and await jobs.tick() == 0
    restored = await PhotoJobs(engine, SimpleNamespace(sessions=sessions)).latest(cid, "preview")
    assert restored["state"] == "ready" and restored["result"]["warnings"] == [
        "pinterest_fallback_used"
    ]
    async with sessions() as session:
        item = (await list_activity(session))[0]
        assert item.state == "warning" and item.label.endswith("Honda Accord")
        assert item.message == "Pinterest недоступен — использован резервный источник"


async def test_photo_job_failure_sanitized_and_exhausted_lease(sessions, monkeypatch):
    async with sessions() as session, session.begin():
        row = Community(domain="bmw")
        session.add(row)
        await session.flush()
        cid = row.id

    async def fail(*args):
        raise RuntimeError("secret token and raw response")

    monkeypatch.setattr("dropgrid.photos.operation_jobs.photo_preview", fail)
    jobs = PhotoJobs(SimpleNamespace(planner=None, visual=None), SimpleNamespace(sessions=sessions))
    await jobs.enqueue(cid, OperationInput(kind="preview"))
    await jobs.tick()
    result = await jobs.latest(cid, "preview")
    assert result["error_code"] == "photo_operation_failed" and "secret" not in str(result)
    await jobs.enqueue(cid, OperationInput(kind="archive"))
    async with sessions() as session, session.begin():
        active = await session.scalar(
            select(PhotoOperationJob).where(PhotoOperationJob.state == "queued")
        )
        active.attempts = 2
        active.lease_until = utcnow() - timedelta(seconds=1)
    await jobs.tick()
    assert (await jobs.latest(cid, "archive"))["error_code"] == "photo_worker_interrupted"


async def test_activity_api_only_safe_read_model(client, sessions):
    async with sessions() as session, session.begin():
        row = Community(domain="test")
        session.add(row)
        await session.flush()
        session.add(
            ReferenceSyncJob(
                community_id=row.id, state="failed", error_code="sensitive-untrusted-data"
            )
        )
    response = await client.get("/api/v1/activity")
    assert response.status_code == 200
    item = response.json()[0]
    assert item["state"] == "failed" and "sensitive" not in response.text
    assert "result" not in item and "payload" not in item


async def test_archive_job_routes_progress_and_recovers_read_result(sessions, monkeypatch):
    from dropgrid.photos.reference_schemas import ArchiveSyncRead

    async with sessions() as session, session.begin():
        row = Community(domain="archive")
        session.add(row)
        await session.flush()
        cid = row.id
    jobs = PhotoJobs(SimpleNamespace(), SimpleNamespace(sessions=sessions))

    async def sync(service, community_id, account_id, max_pages):
        assert community_id == cid and max_pages == 20
        await emit("archive_seek", 3, min_age_days=180, max_age_days=540)
        await emit("archive_scan", 100, 300, photos=12)
        current = await jobs.latest(cid, "archive")
        assert current["counters"]["photos"] == 12 and current["current"] == 100
        return ArchiveSyncRead(posts_scanned=100, candidates_discovered=12)

    monkeypatch.setattr("dropgrid.photos.operation_jobs.ArchiveDiscovery.sync", sync)
    await jobs.enqueue(cid, OperationInput(kind="archive"))
    await jobs.tick()
    row = await jobs.latest(cid, "archive")
    assert row["state"] == "ready" and row["result"]["candidates_discovered"] == 12


async def test_activity_observes_sender_and_monitor_without_exposing_evidence(sessions):
    from dropgrid.db.models import Campaign, Grid, Submission
    from dropgrid.domain.enums import SubmissionStatus

    async with sessions() as session, session.begin():
        grid = Grid(name="Test grid")
        community = Community(domain="test-wall")
        session.add_all([grid, community])
        await session.flush()
        campaign = Campaign(name="Campaign", grid_id=grid.id, track_url="https://vk.com/audio1_2")
        session.add(campaign)
        await session.flush()
        row = Submission(
            campaign_id=campaign.id,
            community_id=community.id,
            status=SubmissionStatus.submitted,
            vk_send_started_at=utcnow() - timedelta(minutes=1),
            submitted_at=utcnow(),
            vk_last_checked_at=utcnow(),
            vk_next_check_at=utcnow() + timedelta(hours=1),
            error_message="untrusted secret",
            vk_monitor_evidence={"secret": "vendor data"},
        )
        session.add(row)
        await session.flush()
        identity = row.id
    async with sessions() as session:
        items = await list_activity(session)
        assert {item.kind for item in items} == {"sending", "monitoring"}
        assert all(item.state == "success" for item in items)
        assert "secret" not in str(items)
    async with sessions() as session, session.begin():
        row = await session.get(Submission, identity)
        row.vk_monitor_lease_until = utcnow() + timedelta(minutes=1)
    async with sessions() as session:
        items = await list_activity(session)
        assert next(item for item in items if item.kind == "monitoring").state == "running"
    async with sessions() as session, session.begin():
        row = await session.get(Submission, identity)
        row.status = SubmissionStatus.pending
        row.vk_send_next_at = utcnow() + timedelta(minutes=1)
    async with sessions() as session:
        items = await list_activity(session)
        assert next(item for item in items if item.kind == "sending").state == "queued"


async def test_activity_keeps_old_active_job_when_recent_history_is_full(sessions):
    async with sessions() as session, session.begin():
        community = Community(domain="long-running")
        session.add(community)
        await session.flush()
        session.add(
            ReferenceSyncJob(
                community_id=community.id,
                state="embedding",
                updated_at=utcnow() - timedelta(days=1),
            )
        )
        session.add_all(
            [ReferenceSyncJob(community_id=community.id, state="ready") for _ in range(105)]
        )
    async with sessions() as session:
        activities = await list_activity(session)
        assert activities[0].state == "running"
        assert sum(item.state == "success" for item in activities) == 100
