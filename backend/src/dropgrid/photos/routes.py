from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from dropgrid.api.dependencies import Session
from dropgrid.db.models import MediaAsset
from dropgrid.photos.domain import PhotoError
from dropgrid.photos.engine import PhotoEngine
from dropgrid.photos.schemas import MediaAssetPage, MediaAssetRead, MediaPlanInput, MediaPlanRead
from dropgrid.services.catalog import NotFoundError, get_entity

router = APIRouter(prefix="/api/v1")


def photo_engine(request: Request) -> PhotoEngine:
    engine: PhotoEngine = request.app.state.photo_engine
    return engine


Engine = Annotated[PhotoEngine, Depends(photo_engine)]


@router.post("/campaigns/{campaign_id}/media/plan", response_model=MediaPlanRead)
async def plan_media(campaign_id: UUID, data: MediaPlanInput, engine: Engine) -> MediaPlanRead:
    # Deliberately no request-long Session dependency during network work.
    return await engine.planner.plan(campaign_id, data)


@router.get("/media-assets", response_model=MediaAssetPage)
async def media_list(
    session: Session,
    category: Annotated[str | None, Query(max_length=200)] = None,
    provider: Annotated[str | None, Query(max_length=50)] = None,
    enabled: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> MediaAssetPage:
    query = select(MediaAsset)
    if category is not None:
        query = query.where(MediaAsset.category == category)
    if provider is not None:
        query = query.where(MediaAsset.provider == provider)
    if enabled is not None:
        query = query.where(MediaAsset.enabled == enabled)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    assets = (
        await session.scalars(
            query.order_by(MediaAsset.created_at.desc(), MediaAsset.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()
    return MediaAssetPage(
        items=[MediaAssetRead.model_validate(a) for a in assets],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/media-assets/{asset_id}", response_model=MediaAssetRead)
async def media_detail(asset_id: UUID, session: Session) -> MediaAsset:
    return await get_entity(session, MediaAsset, asset_id)


@router.get("/media-assets/{asset_id}/content")
async def media_content(asset_id: UUID, session: Session, engine: Engine) -> FileResponse:
    asset = await get_entity(session, MediaAsset, asset_id)
    try:
        path = engine.storage.path(asset.storage_key)
        if not path.is_file() or asset.mime_type != "image/jpeg":
            raise NotFoundError("Media content not found")
    except PhotoError:
        raise NotFoundError("Media content not found") from None
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
