"""Authenticated local inbox operations; no action performs a VK write."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import case, func, select

from dropgrid.api.access import check_reviewer, proxy_reviewer
from dropgrid.api.dependencies import Session
from dropgrid.api.schemas import Input
from dropgrid.db.models import Account, Community, Grid, GridCommunity, PhotoReviewer
from dropgrid.db.models import CommunityDiscoveryCandidate as Candidate
from dropgrid.db.models import CommunityDiscoveryEvidence as Evidence
from dropgrid.db.models import CommunityDiscoveryRun as Run
from dropgrid.discovery.service import RunInput, approve, candidate_read, run_read
from dropgrid.domain.enums import AccountStatus
from dropgrid.photos.regions import REGION_ROWS
from dropgrid.services.catalog import ConflictError, NotFoundError, get_entity

router = APIRouter(prefix="/api/v1/discovery")


@router.get("/options")
async def options(
    session: Session, request: Request, grid_id: UUID | None = None
) -> dict[str, Any]:
    actor = await proxy_reviewer(request, session)
    grids = [
        {"id": row.id, "name": row.name}
        for row in (await session.scalars(select(Grid).order_by(Grid.name, Grid.id))).all()
    ]
    accounts = [
        {"id": row.id, "name": row.name, "vk_user_id": row.vk_user_id}
        for row in (
            await session.scalars(
                select(Account)
                .where(
                    Account.status == AccountStatus.active,
                    Account.encrypted_access_token.is_not(None),
                    Account.vk_user_id.is_not(None),
                )
                .order_by(Account.created_at)
            )
        ).all()
    ]
    seeds = []
    if grid_id:
        seeds = [
            {
                "id": c.id,
                "name": c.name or c.domain,
                "category": r.category,
                "region_key": r.region_key,
            }
            for c, r in (
                await session.execute(
                    select(Community, GridCommunity)
                    .join(GridCommunity, GridCommunity.community_id == Community.id)
                    .where(GridCommunity.grid_id == grid_id)
                    .order_by(Community.domain)
                )
            ).all()
        ]
    return {
        "grids": grids,
        "accounts": accounts,
        "seeds": seeds,
        "regions": [
            {"key": key, "name": name, "codes": codes} for codes, key, name, _ in REGION_ROWS
        ],
        "reviewer": {"id": actor.id, "name": actor.display_name} if actor else None,
        "reviewers": [
            {"id": r.id, "name": r.display_name}
            for r in (
                await session.scalars(select(PhotoReviewer).order_by(PhotoReviewer.display_name))
            ).all()
        ],
    }


@router.post("/runs", status_code=202)
async def start(data: RunInput, request: Request) -> dict[str, Any]:
    result: dict[str, Any] = await request.app.state.discovery.enqueue(data)
    return result


@router.get("/runs")
async def runs(session: Session, grid_id: UUID | None = None) -> list[dict[str, Any]]:
    query = select(Run)
    if grid_id:
        query = query.where(Run.grid_id == grid_id)
    return [
        {**run_read(row), "human_outcomes": await run_outcomes(session, row.id)}
        for row in (await session.scalars(query.order_by(Run.created_at.desc()).limit(50))).all()
    ]


@router.get("/runs/{run_id}")
async def run_detail(run_id: UUID, session: Session) -> dict[str, Any]:
    run = await get_entity(session, Run, run_id)
    from dropgrid.discovery.metrics import quality

    outcomes = await quality(session, run.grid_id)
    return {
        **run_read(run),
        "precision_at_k": outcomes["precision_at_k"].get(str(run_id)),
        "human_outcomes": await run_outcomes(session, run_id),
    }


@router.get("/candidates")
async def candidates(
    session: Session,
    grid_id: UUID,
    run_id: UUID | None = None,
    category: str | None = None,
    region_key: str | None = None,
    status: str = "new",
    minimum_score: float = Query(default=0, ge=0, le=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=60),
) -> dict[str, Any]:
    regional_run = False
    query = select(Candidate).where(Candidate.grid_id == grid_id)
    if run_id:
        run = await get_entity(session, Run, run_id)
        regional_run = any(q.get("region_key") for q in run.queries)
        query = query.join(Evidence, Evidence.candidate_id == Candidate.id).where(
            Evidence.run_id == run_id
        )
    totals = (await session.scalars(select(Candidate).where(Candidate.grid_id == grid_id))).all()
    if status in {"human_approved", "uncertain"}:
        query = query.where(
            Candidate.status == "new",
            Candidate.human_status == ("approved" if status == "human_approved" else "uncertain"),
        )
    elif status == "new":
        query = query.where(Candidate.status == "new", Candidate.human_status.is_(None))
    elif status != "all":
        query = query.where(Candidate.status == status)
    if category:
        query = query.where(Candidate.suggested_category == category)
    if region_key:
        query = query.where(Candidate.suggested_region_key == region_key)
    query = query.where(Candidate.final_score >= minimum_score)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.scalars(
            query.order_by(
                case((Candidate.region_score == 1, 0), else_=1)
                if regional_run
                else Candidate.grid_id,
                Candidate.final_score.desc(),
                Candidate.vk_group_id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [candidate_read(c) for c in rows],
        "total": total,
        "counters": {
            "new": sum(c.status == "new" and c.human_status is None for c in totals),
            "human_approved": sum(
                c.status == "new" and c.human_status == "approved" for c in totals
            ),
            "uncertain": sum(c.status == "new" and c.human_status == "uncertain" for c in totals),
            "recommended": sum(
                c.status == "new" and c.reason.get("confidence") == "high" for c in totals
            ),
            "needs_review": sum(
                c.status == "new" and c.reason.get("confidence") != "high" for c in totals
            ),
            "added": sum(c.status == "approved" for c in totals),
            "rejected": sum(c.status == "rejected" for c in totals),
        },
    }


class DecisionInput(Input):
    action: Literal["reject", "restore", "approve"]
    reviewer_id: UUID
    category: str | None = Field(default=None, max_length=200)
    region_key: str | None = Field(default=None, max_length=64)
    content_hint: str | None = Field(default=None, max_length=3000)
    run_id: UUID | None = None


@router.post("/candidates/{candidate_id}/decision")
async def decision(
    candidate_id: UUID, data: DecisionInput, session: Session, request: Request
) -> dict[str, Any]:
    await check_reviewer(request, session, data.reviewer_id)
    await get_entity(session, PhotoReviewer, data.reviewer_id)
    if data.action == "approve":
        return await approve(
            session,
            candidate_id,
            category=data.category,
            region_key=data.region_key,
            content_hint=data.content_hint,
            reviewer_id=data.reviewer_id,
            run_id=data.run_id,
        )
    row = await session.get(Candidate, candidate_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Discovery candidate not found")
    if data.action == "restore":
        if row.status != "rejected":
            raise ConflictError("Only rejected candidates can be restored")
        row.status, row.classification = "new", "new"
        row.human_status = None
    else:
        if row.status not in {"new", "reviewing"}:
            raise ConflictError("Only inbox candidates can be rejected")
        row.status, row.classification = "rejected", "previously_rejected"
    row.reason = {**row.reason, "decision_by": str(data.reviewer_id)}
    await session.flush()
    return candidate_read(row)


@router.get("/candidates/{candidate_id}/photos/{index}")
async def photo(candidate_id: UUID, index: int, session: Session, request: Request) -> FileResponse:
    row = await get_entity(session, Candidate, candidate_id)
    if not 0 <= index < min(len(row.previews), 6):
        raise NotFoundError("Discovery preview not found")
    path = request.app.state.discovery.storage.path(row.previews[index])
    if not path.is_file():
        raise NotFoundError("Discovery preview expired")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private,max-age=3600"}
    )


async def run_outcomes(session: Session, run_id: UUID) -> dict[str, int]:
    rows = (
        await session.scalars(
            select(Candidate)
            .join(Evidence, Evidence.candidate_id == Candidate.id)
            .where(Evidence.run_id == run_id)
        )
    ).all()
    return {
        s: sum(c.human_status == s for c in rows) for s in ("approved", "rejected", "uncertain")
    }


@router.get("/cache")
async def cache_state(session: Session, request: Request) -> dict[str, Any]:
    import asyncio

    from dropgrid.db.models import DiscoveryCacheState
    from dropgrid.discovery.cache import inspect_cache

    stats = await asyncio.to_thread(inspect_cache, request.app.state.discovery.storage.root)
    state = await session.get(DiscoveryCacheState, 1)
    return {
        **stats,
        "evicted_count": state.evicted_count if state else 0,
        "evictions_since": "community review migration",
        "cleaned_at": state.cleaned_at if state else None,
    }
