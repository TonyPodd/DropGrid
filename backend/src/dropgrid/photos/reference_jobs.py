"""Durable, fenced queue for read-only wall reference study."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from dropgrid.db.models import Community, CommunityContentProfile, ReferenceSyncJob, utcnow
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.photos.reference_schemas import ReferenceSyncInput, ReferenceSyncRead
from dropgrid.photos.references import CommunityReferenceCollector
from dropgrid.services.catalog import get_entity

TERMINAL = ("ready", "failed")


def job_read(row: ReferenceSyncJob) -> dict[str, object]:
    return {
        "id": row.id,
        "community_id": row.community_id,
        "state": row.state,
        "progress": row.progress,
        "result": row.result,
        "error_code": row.error_code,
        "elapsed_seconds": max(
            0,
            int(
                ((row.finished_at or utcnow()) - (row.started_at or row.created_at)).total_seconds()
            ),
        ),
    }


class ReferenceJobs:
    def __init__(self, collector: CommunityReferenceCollector) -> None:
        self.collector, self.sessions = collector, collector.sessions

    async def enqueue(self, community_id: UUID, data: ReferenceSyncInput) -> dict[str, object]:
        try:
            async with self.sessions() as session, session.begin():
                await get_entity(session, Community, community_id)
                profile = await session.get(
                    CommunityContentProfile, community_id, with_for_update=True
                )
                if profile:
                    for until, code in (
                        (profile.archive_lease_until, "archive_sync_in_progress"),
                        (profile.sync_lease_until, "reference_sync_in_progress"),
                        (profile.preview_lease_until, "preview_in_progress"),
                    ):
                        if until and until > utcnow():
                            raise PhotoConflict(code)
                row = ReferenceSyncJob(
                    community_id=community_id,
                    account_id=data.account_id,
                    target_count=data.target_count,
                )
                session.add(row)
                await session.flush()
                return job_read(row)
        except IntegrityError:
            raise PhotoConflict("reference_sync_in_progress") from None

    async def latest(self, community_id: UUID) -> dict[str, object] | None:
        async with self.sessions() as session:
            await get_entity(session, Community, community_id)
            row = await session.scalar(
                select(ReferenceSyncJob)
                .where(ReferenceSyncJob.community_id == community_id)
                .order_by(ReferenceSyncJob.created_at.desc(), ReferenceSyncJob.id)
                .limit(1)
            )
            return job_read(row) if row else None

    async def tick(self) -> int:
        token = uuid4()
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(ReferenceSyncJob)
                .where(
                    ReferenceSyncJob.state.not_in(TERMINAL),
                    (
                        ReferenceSyncJob.lease_until.is_(None)
                        | (ReferenceSyncJob.lease_until <= utcnow())
                    ),
                )
                .order_by(ReferenceSyncJob.created_at, ReferenceSyncJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return 0
            if row.attempts >= 2:
                row.state, row.error_code, row.finished_at = (
                    "failed",
                    "reference_worker_interrupted",
                    utcnow(),
                )
                return 1
            row.attempts += 1
            row.lease_token, row.lease_until = token, utcnow() + timedelta(minutes=8)
            row.started_at = row.started_at or utcnow()
            row.state = "reading_wall"
            identity, community_id, account_id, target = (
                row.id,
                row.community_id,
                row.account_id,
                row.target_count,
            )

        async def progress(state: str, report: ReferenceSyncRead) -> None:
            async with self.sessions() as session, session.begin():
                fresh = await session.get(ReferenceSyncJob, identity, with_for_update=True)
                if not fresh or fresh.lease_token != token:
                    raise PhotoConflict("reference_sync_in_progress")
                fresh.state = state
                fresh.progress = {
                    "posts_scanned": report.posts_scanned,
                    "photo_posts_found": report.photo_posts_found,
                    "downloads_done": report.downloads_succeeded,
                    "downloads_total": report.photo_posts_found - report.references_existing,
                    "embeddings_done": report.embeddings_available,
                    "embeddings_total": report.photo_posts_found,
                }

        result, error = None, None
        try:
            result = await self.collector.sync(community_id, account_id, target, progress=progress)
            if any(
                code.startswith("vk_read_")
                or code in {"sync_timeout", "vk_credentials_unavailable"}
                for code in result.warnings
            ):
                error = "reference_sync_incomplete"
        except Exception:
            # Persist only a fixed code: credentials and vendor responses never enter job state.
            error = "reference_sync_failed"
        async with self.sessions() as session, session.begin():
            fresh = await session.get(ReferenceSyncJob, identity, with_for_update=True)
            if fresh and fresh.lease_token == token:
                fresh.state = "failed" if error else "ready"
                fresh.result = result.model_dump(mode="json") if result else None
                fresh.error_code, fresh.finished_at = error, utcnow()
                fresh.lease_token = fresh.lease_until = None
        return 1
