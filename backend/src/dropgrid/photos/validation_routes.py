"""Human-only review actions. No sender and no external write operations."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from dropgrid.api.access import check_reviewer, proxy_reviewer
from dropgrid.api.dependencies import Session
from dropgrid.api.schemas import Input
from dropgrid.db.models import (
    Campaign,
    PhotoReviewBatch,
    PhotoReviewBatchItem,
    PhotoReviewer,
    utcnow,
)
from dropgrid.photos.review import choose, confirm, latest_selection
from dropgrid.photos.validation import (
    advance,
    batch_summary,
    create_batch,
    item_detail,
    next_alternative,
)
from dropgrid.services.catalog import ConflictError, get_entity

router = APIRouter(prefix="/api/v1")


class ReviewerInput(Input):
    display_name: str = Field(min_length=1, max_length=100)


@router.get("/photo-reviewers")
async def reviewers(session: Session) -> list[dict[str, object]]:
    return [
        dict(id=r.id, display_name=r.display_name)
        for r in (
            await session.scalars(select(PhotoReviewer).order_by(PhotoReviewer.display_name))
        ).all()
    ]


@router.post("/photo-reviewers")
async def reviewer(data: ReviewerInput, session: Session) -> dict[str, object]:
    from sqlalchemy.dialects.postgresql import insert

    await session.execute(
        insert(PhotoReviewer)
        .values(display_name=data.display_name)
        .on_conflict_do_nothing(index_elements=["display_name"])
    )
    row = await session.scalar(
        select(PhotoReviewer).where(PhotoReviewer.display_name == data.display_name)
    )
    assert row
    return dict(id=row.id, display_name=row.display_name)


class BatchInput(Input):
    created_by: UUID | None = None
    name: str = Field(default="Проверка фотографий", min_length=1, max_length=200)
    target_count: int = Field(default=150, ge=1, le=200)
    reviewer_id: UUID | None = None


@router.post("/campaigns/{campaign_id}/review-batches")
async def batch_create(campaign_id: UUID, data: BatchInput, session: Session) -> dict[str, object]:
    return await batch_summary(
        session,
        await create_batch(
            session, campaign_id, data.name, data.target_count, data.reviewer_id, data.created_by
        ),
    )


@router.get("/campaigns/{campaign_id}/review-batches")
async def campaign_batches(campaign_id: UUID, session: Session) -> list[dict[str, object]]:
    await get_entity(session, Campaign, campaign_id)
    return [
        await batch_summary(session, b)
        for b in (
            await session.scalars(
                select(PhotoReviewBatch)
                .where(PhotoReviewBatch.campaign_id == campaign_id)
                .order_by(PhotoReviewBatch.created_at.desc())
            )
        ).all()
    ]


@router.get("/review-batches/{batch_id}")
async def batch_get(batch_id: UUID, session: Session, request: Request) -> dict[str, object]:
    result = await batch_summary(session, await get_entity(session, PhotoReviewBatch, batch_id))
    actor = await proxy_reviewer(request, session)
    if actor:
        result.update(
            reviewer_id=actor.id,
            trusted_reviewer={"id": actor.id, "display_name": actor.display_name},
            owner=getattr(request.state, "remote_user", None) in {"tony", "tima"},
        )
    return result


@router.get("/review-batches/{batch_id}/items/{position}")
async def detail(batch_id: UUID, position: int, session: Session) -> dict[str, object]:
    return await item_detail(
        session, await get_entity(session, PhotoReviewBatch, batch_id), position
    )


class CursorInput(Input):
    position: int = Field(ge=0)
    mode: Literal["all", "pending", "needs_attention", "skipped"] = "pending"
    reviewer_id: UUID | None = None


@router.put("/review-batches/{batch_id}/cursor")
async def cursor(
    batch_id: UUID, data: CursorInput, session: Session, request: Request
) -> dict[str, object]:
    await check_reviewer(request, session, data.reviewer_id)
    batch = await get_entity(session, PhotoReviewBatch, batch_id)
    if data.position >= batch.target_count:
        raise ConflictError("Review position outside batch")
    if data.reviewer_id:
        await get_entity(session, PhotoReviewer, data.reviewer_id)
    batch.current_position, batch.current_filter = data.position, data.mode
    if data.reviewer_id:
        batch.reviewer_id = data.reviewer_id
    return {"saved": True}


class ActionInput(Input):
    selection_id: UUID
    reviewer_id: UUID
    action: Literal["confirm", "skip", "attention", "dislike", "select"]
    rank: int = Field(ge=1, le=12)
    shown_ranks: list[int] = Field(min_length=1, max_length=12)
    expected_confirmed_at: datetime | None = None


@router.post("/review-batches/{batch_id}/items/{position}/action")
async def action(
    batch_id: UUID, position: int, data: ActionInput, request: Request
) -> dict[str, object]:
    planner = request.app.state.photo_engine.planner
    next_rank = None
    async with planner.sessions() as session, session.begin():
        # Same lifecycle lock order as the shared confirmation path.
        batch = await get_entity(session, PhotoReviewBatch, batch_id)
        campaign = await session.get(Campaign, batch.campaign_id, with_for_update=True)
        batch = await session.get(
            PhotoReviewBatch, batch_id, with_for_update=True, populate_existing=True
        )
        item = await session.get(PhotoReviewBatchItem, (batch_id, position), with_for_update=True)
        await check_reviewer(request, session, data.reviewer_id)
        await get_entity(session, PhotoReviewer, data.reviewer_id)
        if (
            not campaign
            or campaign.status.value != "ready"
            or not batch
            or batch.state != "open"
            or not item
        ):
            raise ConflictError("Review is closed or campaign has started sending")
        selection = await latest_selection(session, item.submission_id, True)
        if selection.id != item.selection_session_id or selection.id != data.selection_id:
            raise ConflictError("Review snapshot changed; create a new batch")
        if selection.confirmed_at != data.expected_confirmed_at:
            raise ConflictError("Another reviewer changed this decision; reload")
        if data.rank not in data.shown_ranks:
            raise ConflictError("Chosen candidate must be displayed")
        sid = item.submission_id
        if data.action in {"skip", "attention"}:
            if selection.confirmed_at:
                raise ConflictError("A confirmed choice cannot be silently skipped")
            item.state = "needs_attention" if data.action == "attention" else "skipped"
            item.reviewer_id, item.reviewed_at = data.reviewer_id, utcnow()
            await advance(session, batch, position)
        else:
            await choose(
                session,
                sid,
                data.rank,
                "dislike" if data.action == "dislike" else None,
                data.shown_ranks,
                allow_confirmed=True,
            )
            if data.action == "dislike":
                next_rank = await next_alternative(session, selection, data.rank)
    if data.action == "confirm":
        await confirm(
            planner,
            sid,
            expected_selection_id=data.selection_id,
            expected_rank=data.rank,
            allow_revision=True,
            reviewer_id=data.reviewer_id,
            review_batch_id=batch_id,
            expected_confirmed_at=data.expected_confirmed_at,
        )
    async with planner.sessions() as session:
        result = await batch_summary(session, await get_entity(session, PhotoReviewBatch, batch_id))
        if data.action == "dislike":
            result["next_rank"] = next_rank
        return result


class BatchMetadata(Input):
    created_by: UUID | None = None
    state: Literal["open", "closed"] | None = None


@router.patch("/review-batches/{batch_id}")
async def batch_metadata(
    batch_id: UUID, data: BatchMetadata, session: Session
) -> dict[str, object]:
    context = await get_entity(session, PhotoReviewBatch, batch_id)
    campaign = await session.get(Campaign, context.campaign_id, with_for_update=True)
    batch = await session.get(
        PhotoReviewBatch, batch_id, with_for_update=True, populate_existing=True
    )
    assert batch and campaign
    if data.state == "open" and campaign.status.value != "ready":
        raise ConflictError("Campaign has started sending")
    if "created_by" in data.model_fields_set:
        if data.created_by:
            await get_entity(session, PhotoReviewer, data.created_by)
        batch.created_by = data.created_by
    if data.state:
        batch.state = data.state
    return await batch_summary(session, batch)
