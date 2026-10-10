"""Product workflow endpoints; all photo preparation and review are read/local only."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import func, select

from dropgrid.api.dependencies import Session
from dropgrid.api.schemas import CampaignCreate, Input
from dropgrid.db.models import (
    Campaign,
    CampaignPreparationJob,
    Community,
    PhotoRankingModel,
    PhotoRankingPreference,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
)
from dropgrid.photos.campaign_preparation import enqueue
from dropgrid.photos.learning import latest_model, queue_training, valid_model
from dropgrid.photos.review import choose, confirm, latest_selection
from dropgrid.services.account_pools import pool, save_pool
from dropgrid.services.catalog import ConflictError, get_entity
from dropgrid.services.sending import usable_account

router = APIRouter(prefix="/api/v1")


@router.post("/campaigns/{campaign_id}/retry-failed-photos")
async def retry_failed(campaign_id: UUID, session: Session, request: Request) -> dict[str, object]:
    from dropgrid.services.campaigns import locked_campaign

    await locked_campaign(session, campaign_id)
    failed = list(
        (
            await session.scalars(
                select(Submission.id)
                .where(
                    Submission.campaign_id == campaign_id,
                    Submission.media_asset_id.is_(None),
                    Submission.status == "pending",
                )
                .order_by(Submission.id)
            )
        ).all()
    )
    if not failed:
        return {"total": 0, "state": "ready"}
    job = await enqueue(session, campaign_id, request.app.state.photo_engine, submission_ids=failed)
    return {"total": job.total, "state": job.state}


class WorkflowInput(Input):
    account_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)


class ChoiceInput(Input):
    rank: int = Field(ge=1, le=12)
    rating: Literal["like", "dislike"] | None = None
    shown_ranks: list[int] = Field(default_factory=list, max_length=12)


@router.post("/campaigns/{campaign_id}/prepare-workflow")
async def prepare_workflow(
    campaign_id: UUID, data: WorkflowInput, session: Session, request: Request
) -> dict[str, object]:
    job = await enqueue(session, campaign_id, request.app.state.photo_engine, data.account_ids)
    return {
        "id": job.id,
        "state": job.state,
        "stage": job.stage,
        "total": job.total,
        "completed": job.completed,
    }


class DryRunInput(CampaignCreate):
    account_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)
    grid_id: UUID
    name: str = Field(default="Preparation dry run", min_length=1, max_length=200)
    photo_review_mode: Literal["AUTO", "REVIEW_BEFORE_SEND"] = "AUTO"
    community_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=10000)


@router.post("/campaign-dry-runs")
async def create_dry_run(
    data: DryRunInput, session: Session, request: Request
) -> dict[str, object]:
    from dropgrid.api.schemas import CampaignCreate
    from dropgrid.db.models import GridCommunity
    from dropgrid.services.campaigns import create_campaign

    available = list(
        (
            await session.scalars(
                select(Community.id)
                .join(GridCommunity)
                .where(
                    GridCommunity.grid_id == data.grid_id,
                    Community.is_active.is_(True),
                    Community.resolution_status == "resolved",
                    Community.vk_group_id.is_not(None),
                )
                .order_by(Community.id)
            )
        ).all()
    )
    if data.community_ids is not None:
        if not set(data.community_ids) <= set(available):
            raise ConflictError("Dry-run scope must contain available grid communities")
        available = sorted(set(data.community_ids), key=str)
    if not available:
        raise ConflictError("No available communities in dry-run scope")
    campaign = await create_campaign(
        session,
        CampaignCreate(
            name=data.name,
            grid_id=data.grid_id,
            track_url=data.track_url,
            photo_review_mode=data.photo_review_mode,
        ),
    )
    campaign.is_dry_run, campaign.dry_run_scope = True, [str(cid) for cid in available]
    await session.flush()
    job = await enqueue(session, campaign.id, request.app.state.photo_engine, data.account_ids)
    return {"campaign_id": campaign.id, "is_dry_run": True, "total": job.total, "state": job.state}


@router.get("/campaigns/{campaign_id}/readiness-report")
async def readiness_report(
    campaign_id: UUID, session: Session, request: Request
) -> dict[str, object]:
    from dropgrid.photos.readiness import report

    return await report(
        session,
        campaign_id,
        request.app.state.photo_engine.planner.settings,
        request.app.state.photo_engine.embedder,
    )


@router.get("/campaigns/{campaign_id}/preparation-workflow")
async def preparation_status(campaign_id: UUID, session: Session) -> dict[str, object] | None:
    await get_entity(session, Campaign, campaign_id)
    job = await session.scalar(
        select(CampaignPreparationJob).where(CampaignPreparationJob.campaign_id == campaign_id)
    )
    return (
        {
            "id": job.id,
            "state": job.state,
            "stage": job.stage,
            "total": job.total,
            "completed": job.completed,
            "error_code": job.error_code,
            "result": job.result,
        }
        if job
        else None
    )


@router.put("/campaigns/{campaign_id}/account-pool")
async def update_pool(
    campaign_id: UUID, data: WorkflowInput, session: Session, request: Request
) -> dict[str, object]:
    if data.account_ids is None:
        raise ConflictError("Explicit account selection required")
    await save_pool(session, campaign_id, data.account_ids, request.app.state.vk_client.settings)
    return {"saved": True}


@router.get("/campaigns/{campaign_id}/account-pool")
async def get_pool(
    campaign_id: UUID, session: Session, request: Request
) -> list[dict[str, object]]:
    accounts = await pool(session, campaign_id, request.app.state.vk_client.settings)
    counts: dict[UUID | None, int] = {
        aid: count
        for aid, count in (
            (
                await session.execute(
                    select(Submission.account_id, func.count())
                    .where(Submission.campaign_id == campaign_id)
                    .group_by(Submission.account_id)
                )
            ).all()
        )
    }
    return [
        {
            "account_id": a.id,
            "name": a.name,
            "quota": quota,
            "assigned": counts.get(a.id, 0),
            "usable": usable_account(a),
            "priority": order,
        }
        for a, quota, order in accounts
    ]


@router.get("/campaigns/{campaign_id}/photo-review")
async def photo_review(campaign_id: UUID, session: Session, page: int = 1) -> dict[str, object]:
    await get_entity(session, Campaign, campaign_id)
    page = max(1, page)
    query = (
        select(Submission, Community).join(Community).where(Submission.campaign_id == campaign_id)
    )
    rows = (
        await session.execute(
            query.order_by(Community.domain, Submission.id).limit(25).offset((page - 1) * 25)
        )
    ).all()
    total = (
        await session.scalar(
            select(func.count())
            .select_from(Submission)
            .where(Submission.campaign_id == campaign_id)
        )
        or 0
    )
    prepared = (
        await session.scalar(
            select(func.count())
            .select_from(Submission)
            .where(Submission.campaign_id == campaign_id, Submission.media_asset_id.is_not(None))
        )
        or 0
    )
    attention = (
        await session.scalar(
            select(func.count())
            .select_from(Submission)
            .where(
                Submission.campaign_id == campaign_id,
                func.jsonb_array_length(Submission.photo_attention) > 0,
            )
        )
        or 0
    )
    items = []
    for row, community in rows:
        selection = await session.scalar(
            select(PhotoSelectionSession)
            .where(PhotoSelectionSession.submission_id == row.id)
            .order_by(PhotoSelectionSession.created_at.desc(), PhotoSelectionSession.id.desc())
            .limit(1)
        )
        candidates = (
            (
                await session.scalars(
                    select(PhotoSelectionCandidate)
                    .where(PhotoSelectionCandidate.selection_session_id == selection.id)
                    .order_by(PhotoSelectionCandidate.rank)
                )
            ).all()
            if selection
            else []
        )
        items.append(
            {
                "submission_id": row.id,
                "community_id": community.id,
                "community": community.name or community.domain,
                "media_asset_id": row.media_asset_id,
                "account_id": row.account_id,
                "attention": row.photo_attention,
                "status": row.status,
                "ranking_model_version": row.ranking_model_version,
                "selection_id": selection.id if selection else None,
                "confirmed": bool(selection and selection.confirmed_at),
                "proposed_rank": selection.proposed_rank if selection else None,
                "candidates": [
                    {
                        "rank": c.rank,
                        "provider": c.provider,
                        "source_identity": c.source_identity,
                        "media_asset_id": c.media_asset_id,
                        "preview_id": c.preview_id,
                        "reference_id": c.reference_id,
                        "source_community_id": c.candidate.get("source_community_id", community.id),
                        "features": c.features,
                        "operator_rating": c.operator_rating,
                    }
                    for c in candidates
                ],
            }
        )
    return {
        "items": items,
        "total": total,
        "prepared": prepared,
        "needs_attention": attention,
        "page": page,
    }


@router.put("/submissions/{submission_id}/photo-choice")
async def set_choice(submission_id: UUID, data: ChoiceInput, session: Session) -> dict[str, object]:
    selection = await choose(session, submission_id, data.rank, data.rating, data.shown_ranks)
    return {"proposed_rank": selection.proposed_rank, "confirmed": False}


class ApprovalInput(Input):
    selection_id: UUID | None = None
    proposed_rank: int | None = Field(default=None, ge=1, le=12)
    shown_ranks: list[int] = Field(default_factory=list, max_length=12)


class DisplayedApproval(ApprovalInput):
    submission_id: UUID


class BatchApprovalInput(Input):
    reviews: list[DisplayedApproval] = Field(default_factory=list, max_length=25)


async def record_approval_view(
    session: Session, submission_id: UUID, data: ApprovalInput
) -> tuple[UUID, int]:
    from dropgrid.services.campaigns import locked_campaign

    row = await get_entity(session, Submission, submission_id)
    await locked_campaign(session, row.campaign_id)
    selection = await latest_selection(session, submission_id, True)
    if (data.selection_id and data.selection_id != selection.id) or (
        data.proposed_rank is not None and data.proposed_rank != selection.proposed_rank
    ):
        raise ConflictError("Photo review changed; reload before confirming")
    if not selection.confirmed_at:
        await choose(session, submission_id, selection.proposed_rank, shown_ranks=data.shown_ranks)
    return selection.id, selection.proposed_rank


@router.post("/submissions/{submission_id}/photo-approve")
async def approve_one(
    submission_id: UUID, request: Request, data: ApprovalInput | None = None
) -> dict[str, object]:
    planner = request.app.state.photo_engine.planner
    expected_id = expected_rank = None
    if data:
        async with planner.sessions() as session, session.begin():
            expected_id, expected_rank = await record_approval_view(session, submission_id, data)
    await confirm(
        planner, submission_id, expected_selection_id=expected_id, expected_rank=expected_rank
    )
    return {"confirmed": True}


@router.post("/campaigns/{campaign_id}/photo-approve-all")
async def approve_all(
    campaign_id: UUID, request: Request, data: BatchApprovalInput | None = None
) -> dict[str, object]:
    engine = request.app.state.photo_engine
    async with engine.planner.sessions() as session:
        campaign = await get_entity(session, Campaign, campaign_id)
        rows = list(
            (
                await session.scalars(
                    select(Submission)
                    .where(
                        Submission.campaign_id == campaign_id,
                        Submission.media_asset_id.is_not(None),
                    )
                    .order_by(Submission.id)
                )
            ).all()
        )
    observed: dict[UUID, tuple[UUID, int]] = {}
    if data:
        by_id = {row.id: row for row in rows}
        async with engine.planner.sessions() as session, session.begin():
            for displayed in data.reviews:
                if displayed.submission_id not in by_id:
                    raise ConflictError("Displayed review is outside this campaign approval scope")
                observed[displayed.submission_id] = await record_approval_view(
                    session, displayed.submission_id, displayed
                )
    confirmed = 0
    for row in rows:
        expected_id, expected_rank = observed.get(row.id, (None, None))
        await confirm(
            engine.planner, row.id, expected_selection_id=expected_id, expected_rank=expected_rank
        )
        confirmed += 1
    async with engine.planner.sessions() as session, session.begin():
        campaign = await get_entity(session, Campaign, campaign_id)
        if campaign.status.value == "ready":
            campaign.preparation_state = "ready"
    return {"confirmed": confirmed}


class RankingInput(Input):
    mode: Literal["deterministic", "learned"]


@router.get("/photo-ranking")
async def ranking_status(session: Session, request: Request) -> dict[str, object]:
    count = (
        await session.scalar(
            select(func.count())
            .select_from(PhotoSelectionSession)
            .where(PhotoSelectionSession.confirmed_at.is_not(None))
        )
        or 0
    )
    model = await latest_model(session)
    pref = await session.get(PhotoRankingPreference, 1)
    from dropgrid.db.models import PhotoReviewer

    reviewer_counts = (
        await session.execute(
            select(PhotoReviewer.display_name, func.count())
            .join(PhotoSelectionSession, PhotoSelectionSession.reviewer_id == PhotoReviewer.id)
            .where(PhotoSelectionSession.confirmed_at.is_not(None))
            .group_by(PhotoReviewer.display_name)
        )
    ).all()
    jobs = list(
        (
            await session.scalars(
                select(PhotoRankingModel).where(PhotoRankingModel.state.in_(("queued", "training")))
            )
        ).all()
    )
    return {
        "mode": pref.mode if pref else "deterministic",
        "reviewers": {name: count for name, count in reviewer_counts},
        "choices": count,
        "minimum": request.app.state.vk_client.settings.photo_learned_ranker_min_choices,
        "model_version": str(model.id) if model else None,
        "remaining": max(
            0, request.app.state.vk_client.settings.photo_learned_ranker_min_choices - count
        ),
        "retrain_interval": (
            request.app.state.vk_client.settings.photo_learned_ranker_retrain_choices
        ),
        "latest_model": ("learned:" if pref and pref.mode == "learned" else "shadow:")
        + str(model.id)
        if model
        else "deterministic",
        "metrics": model.metrics if model else None,
        "promotion_ready": bool(
            valid_model(model) and model and model.metrics and model.metrics.get("promotion_ready")
        ),
        "training": bool(jobs),
    }


@router.put("/photo-ranking")
async def set_ranking(data: RankingInput, session: Session) -> dict[str, object]:
    model = await latest_model(session)
    if data.mode == "learned" and not (
        valid_model(model) and model and model.metrics and model.metrics.get("promotion_ready")
    ):
        raise ConflictError(
            "Learned ranking is not ready; deterministic baseline remains available"
        )
    preference = await session.get(PhotoRankingPreference, 1)
    if preference is None:
        preference = PhotoRankingPreference(id=1)
        session.add(preference)
    preference.mode = data.mode
    return {"mode": data.mode}


@router.post("/photo-ranking/train")
async def request_training(session: Session, request: Request) -> dict[str, object]:
    model = await queue_training(session, request.app.state.vk_client.settings, explicit=True)
    return {
        "queued": model is not None,
        "minimum": request.app.state.vk_client.settings.photo_learned_ranker_min_choices,
    }


@router.get("/campaigns/{campaign_id}/results-breakdown")
async def results_breakdown(campaign_id: UUID, session: Session) -> dict[str, object]:
    campaign = await get_entity(session, Campaign, campaign_id)
    from dropgrid.db.models import Account, GridCommunity, MediaAsset

    rows = (
        await session.execute(
            select(Submission, GridCommunity.category, Account.name, MediaAsset.provider)
            .join(
                GridCommunity,
                (GridCommunity.community_id == Submission.community_id)
                & (GridCommunity.grid_id == campaign.grid_id),
            )
            .outerjoin(Account, Account.id == Submission.account_id)
            .outerjoin(MediaAsset, MediaAsset.id == Submission.media_asset_id)
            .where(Submission.campaign_id == campaign_id)
        )
    ).all()
    groups: dict[str, dict[str, dict[str, int | float]]] = {
        kind: {} for kind in ("category", "provider", "account")
    }
    for row, category, account, provider in rows:
        for kind, label in (
            ("category", category or "Без категории"),
            ("provider", row.photo_source or provider or "Без фото"),
            ("account", f"{account} · {row.account_id}" if account else "Не назначен"),
        ):
            bucket = groups[kind].setdefault(label, {"total": 0, "sent": 0})
            bucket["total"] += 1
            status = row.status.value
            bucket[status] = bucket.get(status, 0) + 1
            if row.submitted_at is not None:
                bucket["sent"] += 1
    for values in groups.values():
        for bucket in values.values():
            # Acceptance among successful submissions; pending moderation is included in sent.
            bucket["acceptance_rate"] = (
                bucket.get("published", 0) / bucket["sent"] if bucket["sent"] else 0
            )
    return {"breakdowns": groups}


@router.get("/communities/{community_id}/photo-context")
async def photo_context(
    community_id: UUID, session: Session, grid_id: UUID | None = None
) -> dict[str, object]:
    from dropgrid.db.models import CommunityContentProfile, GridCommunity
    from dropgrid.photos.concepts import retrieval_hint
    from dropgrid.photos.domain import PhotoQueryBuilder

    community = await get_entity(session, Community, community_id)
    relation = await session.get(GridCommunity, (grid_id, community_id)) if grid_id else None
    if grid_id and not relation:
        raise ConflictError("Community is not in this grid")
    profile = await session.get(CommunityContentProfile, community_id)
    category = relation.category if relation else community.category
    comment = relation.comment if relation else None
    hint = retrieval_hint(relation.content_hint if relation else None, community.name, comment)
    plan = PhotoQueryBuilder().build(category, hint, profile.desired_content if profile else None)
    return {
        "category": category,
        "comment": comment,
        "content_hint": plan.content_hint,
        "desired_content": plan.desired_content,
    }


@router.get("/campaigns/{campaign_id}/reuse-audit")
async def audit_reuse(campaign_id: UUID, session: Session) -> dict[str, object]:
    from dropgrid.photos.readiness import reuse_audit

    await get_entity(session, Campaign, campaign_id)
    return await reuse_audit(session, campaign_id)
