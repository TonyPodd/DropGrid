"""Focused community review: all mutations are local and human initiated."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import select

from dropgrid.api.access import check_reviewer, proxy_reviewer
from dropgrid.api.dependencies import Session
from dropgrid.api.schemas import Input
from dropgrid.db.models import CommunityReviewBatch as Batch
from dropgrid.db.models import CommunityReviewBatchItem as Item
from dropgrid.db.models import CommunityReviewDecision as Decision
from dropgrid.db.models import PhotoReviewer
from dropgrid.discovery import review
from dropgrid.discovery.metrics import quality
from dropgrid.services.catalog import NotFoundError, get_entity

router = APIRouter(prefix="/api/v1/discovery")


async def actor_id(request: Request, session: Session, reviewer_id: UUID | None) -> UUID | None:
    actor = await proxy_reviewer(request, session)
    await check_reviewer(request, session, reviewer_id)
    id = actor.id if actor else reviewer_id
    if id:
        await get_entity(session, PhotoReviewer, id)
    return id


class BatchInput(Input):
    grid_id: UUID
    name: str = Field(default="Discovery validation", min_length=1, max_length=200)
    candidate_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=250)
    reviewer_id: UUID | None = None


@router.post("/review-batches", status_code=201)
async def create(data: BatchInput, session: Session, request: Request) -> dict[str, Any]:
    actor = await actor_id(request, session, data.reviewer_id)
    return await review.summary(
        session,
        await review.create_batch(session, data.grid_id, data.name, actor, data.candidate_ids),
        actor,
    )


@router.get("/review-batches")
async def batches(
    grid_id: UUID, session: Session, request: Request, reviewer_id: UUID | None = None
) -> list[dict[str, Any]]:
    actor = await actor_id(request, session, reviewer_id)
    return [
        await review.summary(session, b, actor)
        for b in (
            await session.scalars(
                select(Batch).where(Batch.grid_id == grid_id).order_by(Batch.created_at.desc())
            )
        ).all()
    ]


@router.get("/review-batches/{batch_id}")
async def batch(
    batch_id: UUID, session: Session, request: Request, reviewer_id: UUID | None = None
) -> dict[str, Any]:
    return await review.summary(
        session,
        await get_entity(session, Batch, batch_id),
        await actor_id(request, session, reviewer_id),
    )


@router.get("/review-batches/{batch_id}/items/{position}")
async def detail(
    batch_id: UUID, position: int, session: Session, request: Request, reviewer_id: UUID
) -> dict[str, Any]:
    await actor_id(request, session, reviewer_id)
    return await review.detail(
        session, await get_entity(session, Batch, batch_id), position, reviewer_id
    )


class CursorInput(Input):
    reviewer_id: UUID
    position: int | None = Field(default=None, ge=0)
    mode: Literal["pending", "approved", "rejected", "uncertain", "all"] = "pending"


@router.put("/review-batches/{batch_id}/cursor")
async def cursor(
    batch_id: UUID, data: CursorInput, session: Session, request: Request
) -> dict[str, Any]:
    await actor_id(request, session, data.reviewer_id)
    b = await session.get(Batch, batch_id, with_for_update=True)
    if not b:
        raise NotFoundError("Community review batch not found")
    await review.save_cursor(session, b, data.reviewer_id, data.position, data.mode)
    return await review.summary(session, b, data.reviewer_id)


@router.get("/review-batches/{batch_id}/pending")
async def pending(
    batch_id: UUID, reviewer_id: UUID, session: Session, request: Request
) -> dict[str, Any]:
    await actor_id(request, session, reviewer_id)
    await get_entity(session, Batch, batch_id)
    return {"positions": await review.positions(session, batch_id, reviewer_id, "pending")}


class DecisionInput(Input):
    reviewer_id: UUID
    decision: Literal["approved", "rejected", "uncertain"]
    reason: (
        Literal[
            "wrong_topic",
            "wrong_region",
            "dead_or_inactive",
            "too_broad",
            "commercial_or_irrelevant",
            "duplicate",
            "other",
        ]
        | None
    ) = None
    seen_version: str = Field(min_length=64, max_length=64)
    expected_revision: int = Field(ge=0)


@router.post("/review-batches/{batch_id}/items/{position}/decision")
async def decision(
    batch_id: UUID, position: int, data: DecisionInput, session: Session, request: Request
) -> dict[str, Any]:
    await actor_id(request, session, data.reviewer_id)
    b = await session.get(Batch, batch_id, with_for_update=True)
    if not b:
        raise NotFoundError("Community review batch not found")
    return await review.decide(
        session,
        b,
        position,
        data.reviewer_id,
        data.decision,
        data.reason,
        data.seen_version,
        data.expected_revision,
    )


@router.get("/review-batches/{batch_id}/items/{position}/photos/{index}")
async def photo(
    batch_id: UUID, position: int, index: int, reviewer_id: UUID, session: Session, request: Request
) -> FileResponse:
    await actor_id(request, session, reviewer_id)
    item = await get_entity(session, Item, (batch_id, position))
    label = await session.get(Decision, (batch_id, position, reviewer_id))
    from dropgrid.db.models import CommunityDiscoveryCandidate as Candidate

    candidate = await get_entity(session, Candidate, item.candidate_id)
    keys = label.snapshot.get("preview_cache_keys", []) if label else candidate.previews
    if not 0 <= index < min(12, len(keys)):
        raise NotFoundError("Discovery preview unavailable")
    path = request.app.state.discovery.storage.path(keys[index])
    if not path.is_file():
        raise NotFoundError("Discovery preview expired")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private,max-age=3600"}
    )


@router.get("/quality")
async def metrics(grid_id: UUID, session: Session, batch_id: UUID | None = None) -> dict[str, Any]:
    return await quality(session, grid_id, batch_id)
