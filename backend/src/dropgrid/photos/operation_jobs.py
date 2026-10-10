"""Durable archive/preview work, consumed only by the read-only photo worker."""

from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from dropgrid.api.schemas import Input
from dropgrid.db.models import (
    Account,
    Community,
    CommunityContentProfile,
    PhotoOperationJob,
    ReferenceSyncJob,
    utcnow,
)
from dropgrid.photos.archive import ArchiveDiscovery
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.photos.engine import PhotoEngine
from dropgrid.photos.preview import photo_preview
from dropgrid.photos.progress import observer
from dropgrid.photos.reference_schemas import (
    ArchiveSyncInput,
    ArchiveSyncRead,
    PhotoPreviewInput,
    PhotoPreviewRead,
)
from dropgrid.photos.references import CommunityReferenceCollector
from dropgrid.services.catalog import get_entity


class OperationInput(Input):
    kind: Literal["archive", "preview"]
    prepare: bool = False
    account_id: UUID | None = None
    preview: PhotoPreviewInput = Field(default_factory=PhotoPreviewInput)
    archive: ArchiveSyncInput = Field(default_factory=ArchiveSyncInput)


def job_read(row: PhotoOperationJob) -> dict[str, object]:
    return {
        "id": row.id,
        "community_id": row.community_id,
        "kind": row.kind,
        "state": row.state,
        "stage": row.stage,
        "current": row.current,
        "total": row.total,
        "counters": row.counters,
        "result": row.result,
        "error_code": row.error_code,
        "started_at": row.started_at,
        "updated_at": row.updated_at,
        "elapsed_seconds": max(
            0,
            int(
                ((row.finished_at or utcnow()) - (row.started_at or row.created_at)).total_seconds()
            ),
        ),
    }


class PhotoJobs:
    def __init__(self, engine: PhotoEngine, collector: CommunityReferenceCollector) -> None:
        self.engine, self.collector, self.sessions = engine, collector, collector.sessions

    async def enqueue(self, community_id: UUID, data: OperationInput) -> dict[str, object]:
        try:
            async with self.sessions() as session, session.begin():
                await get_entity(session, Community, community_id)
                await session.execute(
                    insert(CommunityContentProfile)
                    .values(community_id=community_id)
                    .on_conflict_do_nothing()
                )
                profile = await session.get(
                    CommunityContentProfile, community_id, with_for_update=True
                )
                assert profile
                active_reference = await session.scalar(
                    select(ReferenceSyncJob.id)
                    .where(
                        ReferenceSyncJob.community_id == community_id,
                        ReferenceSyncJob.state.not_in(("ready", "failed")),
                    )
                    .limit(1)
                )
                if active_reference:
                    raise PhotoConflict("reference_sync_in_progress")
                for until, code in (
                    (profile.sync_lease_until, "reference_sync_in_progress"),
                    (profile.archive_lease_until, "archive_sync_in_progress"),
                    (profile.preview_lease_until, "preview_in_progress"),
                ):
                    if until and until > utcnow():
                        raise PhotoConflict(code)
                existing = await session.scalar(
                    select(PhotoOperationJob)
                    .where(
                        PhotoOperationJob.community_id == community_id,
                        PhotoOperationJob.state.not_in(("ready", "failed")),
                    )
                    .limit(1)
                )
                if existing:
                    raise PhotoConflict(
                        "archive_sync_in_progress"
                        if existing.kind == "archive"
                        else "preview_in_progress"
                    )
                row = PhotoOperationJob(
                    community_id=community_id, kind=data.kind, payload=data.model_dump(mode="json")
                )
                session.add(row)
                await session.flush()
                return job_read(row)
        except IntegrityError:
            raise PhotoConflict("preview_in_progress") from None

    async def latest(self, community_id: UUID, kind: str) -> dict[str, object] | None:
        async with self.sessions() as session:
            await get_entity(session, Community, community_id)
            row = await session.scalar(
                select(PhotoOperationJob)
                .where(
                    PhotoOperationJob.community_id == community_id, PhotoOperationJob.kind == kind
                )
                .order_by(PhotoOperationJob.created_at.desc(), PhotoOperationJob.id)
                .limit(1)
            )
            return job_read(row) if row else None

    async def tick(self) -> int:
        token = uuid4()
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(PhotoOperationJob)
                .where(
                    PhotoOperationJob.state.not_in(("ready", "failed")),
                    PhotoOperationJob.lease_until.is_(None)
                    | (PhotoOperationJob.lease_until <= utcnow()),
                )
                .order_by(PhotoOperationJob.created_at, PhotoOperationJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return 0
            if row.attempts >= 2:
                row.state, row.error_code, row.finished_at = (
                    "failed",
                    "photo_worker_interrupted",
                    utcnow(),
                )
                return 1
            row.attempts += 1
            row.lease_token, row.lease_until = token, utcnow() + timedelta(minutes=8)
            row.state, row.started_at = "running", row.started_at or utcnow()
            identity, community_id, data = (
                row.id,
                row.community_id,
                OperationInput.model_validate(row.payload),
            )

        async def progress(
            stage: str, current: int, total: int | None, counters: dict[str, int]
        ) -> None:
            async with self.sessions() as session, session.begin():
                fresh = await session.get(PhotoOperationJob, identity, with_for_update=True)
                if not fresh or fresh.lease_token != token:
                    raise PhotoConflict("preview_in_progress")
                fresh.stage, fresh.current, fresh.total = stage, current, total
                fresh.counters = {**fresh.counters, **counters}

        result: ArchiveSyncRead | PhotoPreviewRead | None = None
        error = None
        context = observer.set(progress)
        try:
            if data.kind == "archive":
                result = await ArchiveDiscovery(self.collector).sync(
                    community_id, data.archive.account_id, data.archive.max_pages
                )
            else:
                if data.prepare:
                    from dropgrid.domain.enums import AccountStatus
                    from dropgrid.photos.preparation import MediaPreparation

                    preparation = MediaPreparation(self.collector)
                    async with self.sessions() as session:
                        account = (
                            await session.get(Account, data.account_id)
                            if data.account_id
                            else await session.scalar(
                                select(Account)
                                .where(
                                    Account.status == AccountStatus.active,
                                    Account.encrypted_access_token.is_not(None),
                                )
                                .order_by(Account.id)
                                .limit(1)
                            )
                        )
                    if (
                        account is None
                        or account.status != AccountStatus.active
                        or not account.encrypted_access_token
                    ):
                        raise PhotoConflict("usable_account_required")
                    await progress("communities", 0, 1, {})
                    if data.preview.grid_id:
                        await preparation.prepare_community_media_context(
                            data.preview.grid_id, community_id, account.id
                        )
                    else:
                        from dropgrid.services.vk_accounts import resolve_community

                        async with self.sessions() as session, session.begin():
                            community = await session.get(Community, community_id)
                            if community and community.resolution_status != "resolved":
                                await resolve_community(
                                    session,
                                    community_id,
                                    account.id,
                                    self.collector.client,
                                    self.collector.tokens,
                                )
                        count = await preparation.compatible_count(community_id)
                        if count < preparation.target:
                            await progress(
                                "references", count, preparation.target, {"references_ready": count}
                            )
                            await self.collector.sync(
                                community_id,
                                account.id,
                                preparation.target,
                                max_scanned_posts=100,
                                timeout_seconds=120,
                            )
                    count = await preparation.compatible_count(community_id)
                    await progress(
                        "references", count, preparation.target, {"references_ready": count}
                    )
                result = await photo_preview(
                    self.engine.planner, self.engine.visual, community_id, data.preview
                )
        except Exception:
            error = "photo_operation_failed"
        finally:
            observer.reset(context)
        async with self.sessions() as session, session.begin():
            fresh = await session.get(PhotoOperationJob, identity, with_for_update=True)
            if fresh and fresh.lease_token == token:
                fresh.state, fresh.stage = ("failed", "failed") if error else ("ready", "ready")
                fresh.result = result.model_dump(mode="json") if result else None
                fresh.error_code, fresh.finished_at = error, utcnow()
                fresh.lease_token = fresh.lease_until = None
        return 1
