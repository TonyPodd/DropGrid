"""Bounded displayed choice history and explicit operator decisions."""

from dataclasses import asdict, replace
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import (
    Campaign,
    Community,
    CommunityContentProfile,
    CommunityPhotoFeedback,
    GridCommunity,
    MediaAsset,
    PhotoRankingPreference,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import SubmissionStatus
from dropgrid.photos.domain import PhotoCandidate, PhotoError, PhotoQueryPlan
from dropgrid.photos.learning import latest_model, predict, queue_training, valid_model
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.pool import PoolCandidate, materialize_archive
from dropgrid.services.catalog import ConflictError, get_entity


async def rerank(
    planner: CampaignMediaPlanner, items: list[PoolCandidate], plan: PhotoQueryPlan
) -> tuple[list[PoolCandidate], str]:
    async with planner.sessions() as session:
        model = await latest_model(session)
        preference = await session.get(PhotoRankingPreference, 1)
    if (
        not preference
        or preference.mode != "learned"
        or not valid_model(model)
        or not model
        or not model.metrics
        or not model.metrics.get("promotion_ready")
    ):
        return items, "deterministic-v1"
    from dropgrid.photos.learning import features

    context = PhotoSelectionSession(category=plan.category, content_hint=plan.content_hint)
    scored = []
    for index, item in enumerate(items, 1):
        if not item.score:
            continue
        candidate = PhotoSelectionCandidate(
            rank=index,
            provider=item.source,
            features={**asdict(item.score), "age_reuse_score": item.age_reuse_score},
        )
        vector = features(candidate, context)
        assert model.coefficients
        learned_score = sum(w * x for w, x in zip(model.coefficients, vector, strict=True))
        scored.append((learned_score, item))
    ranked = []
    for score, item in sorted(scored, key=lambda pair: (-pair[0], pair[1].identity)):
        assert item.score
        item.score = replace(item.score, final_score=score)
        ranked.append(item)
    return ranked, "pairwise-v1:" + str(model.id)


async def snapshot(
    session: AsyncSession,
    submission: Submission,
    plan: PhotoQueryPlan,
    items: list[PoolCandidate],
    baseline: list[PoolCandidate],
    model_version: str,
    warnings: list[str],
) -> None:
    viable = [
        item
        for item in items
        if item.photo.publication_eligible or item.photo.provider == "pinterest"
    ]
    chosen = next(
        (item for item in viable if item.asset and item.asset.id == submission.media_asset_id), None
    )
    if not chosen:
        submission.photo_attention = ["preparation_error"]
        return
    shown = viable[:12]
    if chosen not in shown:
        shown = shown[:11] + [chosen]
    chosen_rank = shown.index(chosen) + 1
    baseline_choice = next((item for item in baseline if item in shown), chosen)
    context = PhotoSelectionSession(
        campaign_id=submission.campaign_id,
        submission_id=submission.id,
        community_id=submission.community_id,
        category=plan.category,
        content_hint=plan.content_hint,
        baseline_rank=shown.index(baseline_choice) + 1,
        proposed_rank=chosen_rank,
        ranking_model_version=model_version,
    )
    session.add(context)
    await session.flush()
    rows = []
    for rank, item in enumerate(shown, 1):
        assert item.score
        feedback = await session.get(
            CommunityPhotoFeedback, (submission.community_id, *item.identity)
        )
        row = PhotoSelectionCandidate(
            selection_session_id=context.id,
            rank=rank,
            provider=item.source,
            source_identity=item.photo.provider_asset_id,
            media_asset_id=item.asset.id if item.asset else None,
            preview_id=item.preview_id,
            reference_id=item.reference.id if item.reference else None,
            features={
                **asdict(item.score),
                "age_reuse_score": item.age_reuse_score,
                "context_desired_content": plan.desired_content,
                "baseline_rank": baseline.index(item) + 1 if item in baseline else rank,
            },
            candidate={
                **item.photo.model_dump(mode="json"),
                **(
                    {"source_community_id": str(item.reference.community_id)}
                    if item.reference
                    else {}
                ),
            },
            operator_rating=feedback.rating if feedback else None,
        )
        session.add(row)
        rows.append(row)
    context.learned_rank = predict(rows, context, await latest_model(session))
    submission.ranking_model_version = model_version
    submission.photo_source = chosen.source
    attention = [flag for flag in submission.photo_attention if flag in {"community_unavailable"}]
    assert chosen.score
    if not chosen.score.reference_count:
        attention.append("no_compatible_core_references")
    if chosen.embedding is None:
        attention.append("no_candidate_embedding")
    if len(viable) < 5:
        attention.append("small_candidate_pool")
    if all(item.source == "pixabay" for item in viable):
        attention.append("fallback_only")
    if "pinterest_search_unavailable" in warnings:
        attention.append("pinterest_unavailable")
    if rows[chosen_rank - 1].operator_rating == "dislike":
        attention.append("operator_disliked")
    submission.photo_attention = attention


async def latest_selection(
    session: AsyncSession, submission_id: UUID, lock: bool = False
) -> PhotoSelectionSession:
    query = (
        select(PhotoSelectionSession)
        .where(PhotoSelectionSession.submission_id == submission_id)
        .order_by(PhotoSelectionSession.created_at.desc(), PhotoSelectionSession.id.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if not row:
        raise ConflictError("Photo review not prepared")
    return row


async def choose(
    session: AsyncSession,
    submission_id: UUID,
    rank: int,
    rating: str | None = None,
    shown_ranks: list[int] | None = None,
) -> PhotoSelectionSession:
    submission = await get_entity(session, Submission, submission_id)
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == submission.campaign_id).with_for_update()
    )
    assert campaign
    selection = await latest_selection(session, submission_id, True)
    if (
        campaign.status.value != "ready"
        or submission.status != SubmissionStatus.pending
        or submission.vk_send_phase is not None
        or selection.confirmed_at
    ):
        raise ConflictError("Review is immutable after confirmation or sending")
    candidate = await session.get(PhotoSelectionCandidate, (selection.id, rank))
    if candidate is None:
        raise ConflictError("Candidate was not displayed in this review")
    selection.proposed_rank = rank
    campaign.preparation_state = "awaiting_review"
    displayed = (
        await session.scalars(
            select(PhotoSelectionCandidate).where(
                PhotoSelectionCandidate.selection_session_id == selection.id
            )
        )
    ).all()
    actual = {item.rank for item in displayed}
    if shown_ranks and not set(shown_ranks) <= actual:
        raise ConflictError("Only displayed candidates may be labelled")
    for item in displayed:
        item.displayed = item.displayed or item.rank in (shown_ranks or [rank])
    if rating is not None:
        if rating not in {"like", "dislike"}:
            raise ConflictError("Invalid rating")
        candidate.operator_rating = rating
    return selection


async def confirm(
    planner: CampaignMediaPlanner,
    submission_id: UUID,
    *,
    expected_selection_id: UUID | None = None,
    expected_rank: int | None = None,
) -> None:
    from dropgrid.integrations.vk.read_only import preparation_read_only

    marker = preparation_read_only.set(True)
    try:
        await _confirm(
            planner,
            submission_id,
            expected_selection_id=expected_selection_id,
            expected_rank=expected_rank,
        )
    finally:
        preparation_read_only.reset(marker)


async def _confirm(
    planner: CampaignMediaPlanner,
    submission_id: UUID,
    *,
    expected_selection_id: UUID | None = None,
    expected_rank: int | None = None,
) -> None:
    async with planner.sessions() as session:
        row = await get_entity(session, Submission, submission_id)
        selection = await latest_selection(session, submission_id)
        review_campaign = await get_entity(session, Campaign, row.campaign_id)
        if (expected_selection_id is not None and selection.id != expected_selection_id) or (
            expected_rank is not None and selection.proposed_rank != expected_rank
        ):
            raise ConflictError("Photo review changed; reload before confirming")
        if selection.confirmed_at:
            return
        candidate = await session.get(
            PhotoSelectionCandidate, (selection.id, selection.proposed_rank)
        )
        assert candidate
        proposed = selection.proposed_rank
        asset = (
            await session.get(MediaAsset, candidate.media_asset_id)
            if candidate.media_asset_id
            else None
        )
    if asset is None:
        photo = PhotoCandidate.model_validate(
            {k: v for k, v in candidate.candidate.items() if k != "source_community_id"}
        )
        if candidate.provider in {"vk_archive", "vk_category_archive"}:
            from dropgrid.db.models import CommunityReferencePhoto

            if not planner.archive or not candidate.reference_id:
                raise ConflictError("Archive candidate unavailable")
            async with planner.sessions() as session:
                reference = await session.get(CommunityReferencePhoto, candidate.reference_id)
            if not reference:
                raise ConflictError("Archive candidate unavailable")
            item = PoolCandidate(photo, candidate.provider, reference=reference)
            asset, _ = await materialize_archive(
                planner.archive,
                planner,
                item,
                selection.category or "",
                target_id=row.community_id,
                grid_id=review_campaign.grid_id,
            )
        else:
            asset, _, error = await planner._import(photo, selection.category or "", False)
            if not asset:
                raise PhotoError(error or "candidate_unavailable")
    if not planner.eligible(asset):
        raise ConflictError("Selected media is no longer usable")
    async with planner.sessions() as session, session.begin():
        row = await get_entity(session, Submission, submission_id)
        campaign = await session.scalar(
            select(Campaign).where(Campaign.id == row.campaign_id).with_for_update()
        )
        assert campaign
        await session.refresh(row, with_for_update=True)
        fresh = await latest_selection(session, submission_id, True)
        if fresh.confirmed_at:
            return
        if (
            fresh.id != selection.id
            or fresh.proposed_rank != proposed
            or row.status != SubmissionStatus.pending
            or row.vk_send_phase is not None
            or campaign.status.value != "ready"
        ):
            raise ConflictError("Review context changed")
        relation = await session.get(GridCommunity, (campaign.grid_id, row.community_id))
        community = await session.get(Community, row.community_id)
        profile = await session.get(CommunityContentProfile, row.community_id)
        from dropgrid.photos.concepts import retrieval_hint
        from dropgrid.photos.domain import normalize_category

        if "context_desired_content" in candidate.features:
            current_hint = retrieval_hint(
                relation.content_hint if relation else None,
                community.name if community else None,
                relation.comment if relation else None,
            )
            if (
                not relation
                or normalize_category(relation.category) != normalize_category(fresh.category)
                or current_hint != fresh.content_hint
                or (profile.desired_content if profile else None)
                != candidate.features["context_desired_content"]
            ):
                raise ConflictError("Photo context changed; prepare again")
        if row.media_asset_id != asset.id:
            reused = (
                await session.scalar(
                    select(func.count())
                    .select_from(Submission)
                    .where(
                        Submission.media_asset_id == asset.id,
                        Submission.campaign_id == row.campaign_id,
                        Submission.status == SubmissionStatus.pending,
                        Submission.id != row.id,
                    )
                )
                or 0
            )
            if reused >= planner.settings.photo_max_reuse_per_asset:
                raise ConflictError("Photo reuse limit reached")
        from dropgrid.photos.rotation import community_usage, recently_used

        usage = await community_usage(session, row.community_id, utcnow())
        if recently_used(
            usage,
            asset.provider or "library",
            asset.provider_asset_id or str(asset.id),
            asset.sha256,
            asset.perceptual_hash,
            utcnow(),
            asset.id,
        ):
            raise ConflictError("Photo was recently used in this community")
        row.media_asset_id = asset.id
        row.photo_source = candidate.provider
        row.photo_attention = [flag for flag in row.photo_attention if flag != "operator_disliked"]
        if candidate.operator_rating == "dislike":
            row.photo_attention = [*row.photo_attention, "operator_disliked"]
        fresh.chosen_rank, fresh.confirmed_at = proposed, utcnow()
        candidates = (
            await session.scalars(
                select(PhotoSelectionCandidate).where(
                    PhotoSelectionCandidate.selection_session_id == fresh.id
                )
            )
        ).all()
        for displayed in candidates:
            displayed.selected = displayed.rank == proposed
            if displayed.selected:
                displayed.media_asset_id = asset.id
                displayed.displayed = True
        if campaign.photo_review_mode == "AUTO":
            campaign.preparation_state = "ready"
        else:
            current_sessions = (
                await session.scalars(
                    select(PhotoSelectionSession)
                    .where(PhotoSelectionSession.campaign_id == campaign.id)
                    .distinct(PhotoSelectionSession.submission_id)
                    .order_by(
                        PhotoSelectionSession.submission_id,
                        PhotoSelectionSession.created_at.desc(),
                        PhotoSelectionSession.id.desc(),
                    )
                )
            ).all()
            if all(decision.confirmed_at for decision in current_sessions):
                campaign.preparation_state = "ready"
        await queue_training(session, planner.settings)
