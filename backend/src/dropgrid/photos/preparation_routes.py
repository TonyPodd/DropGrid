from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import func, select

from dropgrid.api.dependencies import VK, Session, Tokens
from dropgrid.api.schemas import Input
from dropgrid.db.models import Grid, MediaPreparationJob
from dropgrid.photos.preparation import (
    GridReadiness,
    MediaContext,
    MediaPreparation,
    PreparationInput,
    PreparationJobRead,
)
from dropgrid.services.catalog import get_entity
from dropgrid.services.community_resolution import resolve_grid_chunk

router = APIRouter(prefix="/api/v1")


def preparation(request: Request) -> MediaPreparation:
    service: MediaPreparation = request.app.state.media_preparation
    return service


Prep = Annotated[MediaPreparation, Depends(preparation)]


class ResolveChunk(Input):
    account_id: UUID
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=25)


@router.post("/grids/{grid_id}/resolve")
async def resolve_chunk(
    grid_id: UUID, data: ResolveChunk, service: Prep, client: VK, tokens: Tokens
) -> dict[str, object]:
    results = await resolve_grid_chunk(
        service.sessions, client, tokens, grid_id, data.account_id, data.offset, data.limit
    )
    return {
        "offset": data.offset,
        "processed": len(results),
        "items": [
            {
                "reference": item.reference,
                "status": item.status,
                "vk_group_id": item.group.id if item.group else None,
                "error_code": item.error_code,
            }
            for item in results
        ],
    }


@router.get("/grids/{grid_id}/readiness", response_model=GridReadiness)
async def readiness(grid_id: UUID, service: Prep) -> GridReadiness:
    return await service.readiness(grid_id)


@router.post("/grids/{grid_id}/media-preparation", status_code=202)
async def queue_grid(grid_id: UUID, data: PreparationInput, service: Prep) -> dict[str, int]:
    return {"selected": await service.enqueue(grid_id, data)}


@router.post("/campaigns/{campaign_id}/media-preparation", status_code=202)
async def queue_campaign(
    campaign_id: UUID, data: PreparationInput, service: Prep
) -> dict[str, int]:
    return {"selected": await service.enqueue_campaign(campaign_id, data)}


@router.get(
    "/grids/{grid_id}/communities/{community_id}/media-context", response_model=MediaContext
)
async def context(grid_id: UUID, community_id: UUID, service: Prep) -> MediaContext:
    return await service.context(grid_id, community_id)


@router.get("/grids/{grid_id}/media-preparation")
async def jobs(
    grid_id: UUID, session: Session, page: Annotated[int, Query(ge=1)] = 1
) -> dict[str, object]:
    await get_entity(session, Grid, grid_id)
    query = select(MediaPreparationJob).where(MediaPreparationJob.grid_id == grid_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.scalars(
            query.order_by(MediaPreparationJob.community_id, MediaPreparationJob.account_id)
            .offset((page - 1) * 25)
            .limit(25)
        )
    ).all()
    return {
        "items": [PreparationJobRead.model_validate(row) for row in rows],
        "total": total or 0,
        "page": page,
        "page_size": 25,
    }
