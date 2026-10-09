from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from dropgrid.api.dependencies import Session
from dropgrid.db.models import (
    Community,
    CommunityPhotoFeedback,
    CommunityReferencePhoto,
    PhotoPreviewCache,
    utcnow,
)
from dropgrid.photos.archive import ArchiveDiscovery
from dropgrid.photos.domain import PhotoError
from dropgrid.photos.operation_jobs import OperationInput, PhotoJobs
from dropgrid.photos.preview import photo_preview
from dropgrid.photos.reference_jobs import ReferenceJobs
from dropgrid.photos.reference_schemas import (
    ArchiveSyncInput,
    ArchiveSyncRead,
    PhotoFeedbackInput,
    PhotoFeedbackRead,
    PhotoPreviewInput,
    PhotoPreviewRead,
    ProfileInput,
    ProfileRead,
    ReferencePage,
    ReferenceRead,
    ReferenceSyncInput,
    ReferenceSyncRead,
)
from dropgrid.photos.references import CommunityReferenceCollector, profile_read, profile_save
from dropgrid.photos.routes import Engine
from dropgrid.services.catalog import NotFoundError, get_entity

router = APIRouter(prefix="/api/v1")


def collector(request: Request) -> CommunityReferenceCollector:
    value: CommunityReferenceCollector = request.app.state.reference_collector
    return value


Collector = Annotated[CommunityReferenceCollector, Depends(collector)]


@router.get("/communities/{community_id}/content-profile", response_model=ProfileRead)
async def get_profile(community_id: UUID, session: Session) -> ProfileRead:
    return await profile_read(session, community_id)


@router.put("/communities/{community_id}/content-profile", response_model=ProfileRead)
async def put_profile(community_id: UUID, data: ProfileInput, session: Session) -> ProfileRead:
    return await profile_save(session, community_id, data)


@router.post("/communities/{community_id}/references/sync", response_model=ReferenceSyncRead)
async def sync_references(
    community_id: UUID, service: Collector, data: ReferenceSyncInput | None = None
) -> ReferenceSyncRead:
    values = data or ReferenceSyncInput()
    return await service.sync(community_id, values.account_id, values.target_count)


@router.get("/communities/{community_id}/references", response_model=ReferencePage)
async def reference_list(
    community_id: UUID,
    session: Session,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ReferencePage:
    await get_entity(session, Community, community_id)
    query = select(CommunityReferencePhoto).where(
        CommunityReferencePhoto.community_id == community_id,
        CommunityReferencePhoto.is_style_reference.is_(True),
    )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.scalars(
            query.order_by(CommunityReferencePhoto.posted_at.desc(), CommunityReferencePhoto.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()
    return ReferencePage(
        items=[ReferenceRead.model_validate(row) for row in rows],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/communities/{community_id}/references/{reference_id}/content")
async def reference_content(
    community_id: UUID, reference_id: UUID, session: Session, engine: Engine
) -> FileResponse:
    row = await session.get(CommunityReferencePhoto, reference_id)
    if row is None:
        raise NotFoundError("Reference content not found")
    if row.community_id != community_id or not row.storage_key:
        raise NotFoundError("Reference content not found")
    try:
        path = engine.reference_storage.path(row.storage_key)
        if not path.is_file():
            raise NotFoundError("Reference content not found")
    except PhotoError:
        raise NotFoundError("Reference content not found") from None
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=86400"},
    )


@router.post("/communities/{community_id}/photo-preview", response_model=PhotoPreviewRead)
async def preview_photos(
    community_id: UUID, engine: Engine, data: PhotoPreviewInput | None = None
) -> PhotoPreviewRead:
    return await photo_preview(
        engine.planner, engine.visual, community_id, data or PhotoPreviewInput()
    )


@router.post("/communities/{community_id}/archive/sync", response_model=ArchiveSyncRead)
async def archive_sync(
    community_id: UUID, service: Collector, data: ArchiveSyncInput | None = None
) -> ArchiveSyncRead:
    values = data or ArchiveSyncInput()
    return await ArchiveDiscovery(service).sync(community_id, values.account_id, values.max_pages)


@router.post("/communities/{community_id}/references/jobs", status_code=202)
async def queue_reference_study(
    community_id: UUID, service: Collector, data: ReferenceSyncInput | None = None
) -> dict[str, object]:
    return await ReferenceJobs(service).enqueue(community_id, data or ReferenceSyncInput())


@router.get("/communities/{community_id}/references/jobs/latest")
async def latest_reference_study(
    community_id: UUID, service: Collector
) -> dict[str, object] | None:
    return await ReferenceJobs(service).latest(community_id)


@router.get("/photo-previews/{preview_id}/content")
async def pin_preview_content(preview_id: UUID, session: Session, engine: Engine) -> FileResponse:
    row = await session.get(PhotoPreviewCache, preview_id)
    if not row or row.provider != "pinterest":
        raise NotFoundError("Preview content not found")
    try:
        path = engine.pinterest_storage.path(row.storage_key)
        if not path.is_file():
            raise NotFoundError("Preview content not found")
    except PhotoError:
        raise NotFoundError("Preview content not found") from None
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=86400"},
    )


@router.post("/communities/{community_id}/photo-jobs", status_code=202)
async def queue_photo_operation(
    community_id: UUID, data: OperationInput, engine: Engine, service: Collector
) -> dict[str, object]:
    return await PhotoJobs(engine, service).enqueue(community_id, data)


@router.get("/communities/{community_id}/photo-jobs/latest")
async def latest_photo_operation(
    community_id: UUID,
    engine: Engine,
    service: Collector,
    kind: Annotated[str, Query(pattern="^(archive|preview)$")] = "preview",
) -> dict[str, object] | None:
    return await PhotoJobs(engine, service).latest(community_id, kind)


@router.get("/communities/{community_id}/photo-feedback", response_model=list[PhotoFeedbackRead])
async def photo_feedback_list(community_id: UUID, session: Session) -> list[PhotoFeedbackRead]:
    await get_entity(session, Community, community_id)
    rows = (
        await session.scalars(
            select(CommunityPhotoFeedback)
            .where(CommunityPhotoFeedback.community_id == community_id)
            .order_by(CommunityPhotoFeedback.created_at.desc())
            .limit(500)
        )
    ).all()
    return [PhotoFeedbackRead.model_validate(r) for r in rows]


@router.put("/communities/{community_id}/photo-feedback", response_model=PhotoFeedbackRead)
async def photo_feedback_save(
    community_id: UUID, data: PhotoFeedbackInput, session: Session
) -> PhotoFeedbackRead:
    await get_entity(session, Community, community_id)
    await session.execute(
        insert(CommunityPhotoFeedback)
        .values(community_id=community_id, **data.model_dump(), created_at=utcnow())
        .on_conflict_do_update(
            index_elements=["community_id", "provider", "source_identity"],
            set_={"rating": data.rating},
        )
    )
    row = await session.get(
        CommunityPhotoFeedback,
        (community_id, data.provider, data.source_identity),
        populate_existing=True,
    )
    return PhotoFeedbackRead.model_validate(row)


@router.delete("/communities/{community_id}/photo-feedback", status_code=204)
async def photo_feedback_delete(
    community_id: UUID, provider: str, source_identity: str, session: Session
) -> None:
    await get_entity(session, Community, community_id)
    await session.execute(
        delete(CommunityPhotoFeedback).where(
            CommunityPhotoFeedback.community_id == community_id,
            CommunityPhotoFeedback.provider == provider,
            CommunityPhotoFeedback.source_identity == source_identity,
        )
    )
