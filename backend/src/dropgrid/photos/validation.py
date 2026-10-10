"""Server-persisted, source-balanced human validation over existing snapshots."""

from collections import Counter, defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import (
    Campaign,
    Community,
    PhotoReviewBatch,
    PhotoReviewBatchItem,
    PhotoReviewer,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
)
from dropgrid.photos.learning import number
from dropgrid.services.catalog import ConflictError, get_entity

FILTERS = {"all", "pending", "needs_attention", "skipped"}


def stratify(
    rows: list[tuple[PhotoSelectionSession, Submission]],
    candidates: dict[UUID, list[PhotoSelectionCandidate]],
    count: int,
) -> list[PhotoSelectionSession]:
    """Deterministic coverage of six axes; cross-source competition is sampled first."""
    axes: dict[UUID, tuple[str, ...]] = {}
    competition = []
    for selection, submission in rows:
        options = candidates.get(selection.id, [])
        winner = next((c for c in options if c.rank == selection.proposed_rank), None)
        providers = {c.provider for c in options}
        scores = sorted([number(c.features.get("final_score")) for c in options], reverse=True)
        axes[selection.id] = (
            selection.category or "uncategorized",
            winner.provider if winner else "unknown",
            "hint" if selection.content_hint else "no_hint",
            "attention" if submission.photo_attention else "normal",
            "close" if len(scores) > 1 and scores[0] - scores[1] <= 0.03 else "obvious",
            "diverse" if len(providers) > 1 else "single_source",
        )
        if {"pinterest", "vk_category_archive"} <= providers:
            competition.append(selection)
    counts: Counter[tuple[int, str]] = Counter()
    selected: list[PhotoSelectionSession] = []

    def add(item: PhotoSelectionSession) -> None:
        selected.append(item)
        counts.update(enumerate(axes[item.id]))

    # Cap at one quarter, leaving room for every other stratum.
    for item in sorted(competition, key=lambda s: (s.category or "", str(s.id)))[
        : max(1, count // 4)
    ]:
        add(item)
    remaining = [s for s, _ in rows if s not in selected]
    while remaining and len(selected) < count:
        item = min(
            remaining,
            key=lambda s: (
                -sum(
                    (2 if i == 0 else 1) / (1 + counts[i, value])
                    for i, value in enumerate(axes[s.id])
                ),
                s.category or "",
                str(s.community_id),
                str(s.id),
            ),
        )
        add(item)
        remaining.remove(item)
    return selected


async def create_batch(
    session: AsyncSession,
    campaign_id: UUID,
    name: str,
    count: int,
    reviewer_id: UUID | None,
    created_by: UUID | None = None,
    *,
    selection_ids: set[UUID] | None = None,
) -> PhotoReviewBatch:
    campaign = await get_entity(session, Campaign, campaign_id)
    if campaign.status.value != "ready" or campaign.preparation_state == "preparing":
        raise ConflictError("Prepare campaign photos before validation")
    if reviewer_id:
        await get_entity(session, PhotoReviewer, reviewer_id)
    if created_by:
        await get_entity(session, PhotoReviewer, created_by)
    selections = list(
        (
            await session.scalars(
                select(PhotoSelectionSession)
                .where(PhotoSelectionSession.campaign_id == campaign_id)
                .distinct(PhotoSelectionSession.submission_id)
                .order_by(
                    PhotoSelectionSession.submission_id,
                    PhotoSelectionSession.created_at.desc(),
                    PhotoSelectionSession.id.desc(),
                )
            )
        ).all()
    )
    submissions = {
        s.id: s
        for s in (
            await session.scalars(select(Submission).where(Submission.campaign_id == campaign_id))
        ).all()
    }
    grouped: dict[UUID, list[PhotoSelectionCandidate]] = defaultdict(list)
    for c in (
        await session.scalars(
            select(PhotoSelectionCandidate)
            .where(PhotoSelectionCandidate.selection_session_id.in_([s.id for s in selections]))
            .order_by(PhotoSelectionCandidate.rank)
        )
    ).all():
        grouped[c.selection_session_id].append(c)
    rows = [
        (s, submissions[s.submission_id])
        for s in selections
        if submissions[s.submission_id].media_asset_id
        and grouped[s.id]
        and (selection_ids is None or s.id in selection_ids)
    ]
    if len(rows) < count:
        raise ConflictError("Not enough prepared snapshots for requested batch")
    batch = PhotoReviewBatch(
        campaign_id=campaign_id,
        name=name,
        target_count=count,
        reviewer_id=reviewer_id,
        created_by=created_by,
    )
    session.add(batch)
    await session.flush()
    for position, selection in enumerate(stratify(rows, grouped, count)):
        session.add(
            PhotoReviewBatchItem(
                batch_id=batch.id,
                position=position,
                submission_id=selection.submission_id,
                selection_session_id=selection.id,
                automatic_rank=selection.proposed_rank,
                state="confirmed" if selection.confirmed_at else "pending",
                reviewer_id=selection.reviewer_id,
                reviewed_at=selection.confirmed_at,
            )
        )
    await session.flush()
    return batch


async def advance(session: AsyncSession, batch: PhotoReviewBatch, position: int) -> None:
    await session.flush()
    positions = list(
        (
            await session.scalars(
                select(PhotoReviewBatchItem.position)
                .where(
                    PhotoReviewBatchItem.batch_id == batch.id,
                    PhotoReviewBatchItem.state == "pending",
                )
                .order_by(PhotoReviewBatchItem.position)
            )
        ).all()
    )
    batch.current_filter = "pending"
    batch.current_position = next(
        (p for p in positions if p > position), positions[0] if positions else position
    )


async def batch_summary(session: AsyncSession, batch: PhotoReviewBatch) -> dict[str, object]:
    rows = (
        await session.execute(
            select(PhotoReviewBatchItem, PhotoSelectionSession, Submission)
            .join(
                PhotoSelectionSession,
                PhotoSelectionSession.id == PhotoReviewBatchItem.selection_session_id,
            )
            .join(Submission, Submission.id == PhotoReviewBatchItem.submission_id)
            .where(PhotoReviewBatchItem.batch_id == batch.id)
            .order_by(PhotoReviewBatchItem.position)
        )
    ).all()
    sources: Counter[str] = Counter()
    selected_sources: Counter[str] = Counter()
    displayed_sources: Counter[str] = Counter()
    category: Counter[str] = Counter()
    grouped: dict[UUID, list[PhotoSelectionCandidate]] = defaultdict(list)
    for c in (
        await session.scalars(
            select(PhotoSelectionCandidate)
            .where(PhotoSelectionCandidate.selection_session_id.in_([s.id for _, s, _ in rows]))
            .order_by(PhotoSelectionCandidate.rank)
        )
    ).all():
        grouped[c.selection_session_id].append(c)
    states = Counter(item.state for item, _, _ in rows)
    replaced = 0
    both = 0
    strong = 0
    items = []
    for item, selection, submission in rows:
        options = grouped[selection.id]
        winner = next((c for c in options if c.rank == item.automatic_rank), None)
        sources[winner.provider if winner else "unknown"] += 1
        displayed_sources.update(c.provider for c in options)
        category[selection.category or "Без категории"] += 1
        providers = {c.provider for c in options}
        both += int({"pinterest", "vk_category_archive"} <= providers)
        pin = next((c for c in options if c.provider == "pinterest"), None)
        strong += int(
            bool(
                winner
                and winner.provider == "vk_category_archive"
                and pin
                and number(winner.features.get("final_score"))
                - number(pin.features.get("final_score"))
                <= 0.1
            )
        )
        if item.state == "confirmed":
            replaced += int(selection.chosen_rank != item.automatic_rank)
            chosen = next((c for c in options if c.rank == selection.chosen_rank), None)
            if chosen:
                selected_sources[chosen.provider] += 1
        items.append(
            dict(
                position=item.position,
                state=item.state,
                attention=bool(submission.photo_attention),
                reviewer_id=item.reviewer_id,
                reviewed_at=item.reviewed_at,
            )
        )
    done = states["confirmed"] + states["skipped"] + states["needs_attention"]
    return dict(
        id=batch.id,
        name=batch.name,
        created_by=batch.created_by,
        created_at=batch.created_at,
        campaign_id=batch.campaign_id,
        state=batch.state,
        target_count=batch.target_count,
        reviewer_id=batch.reviewer_id,
        current_position=batch.current_position,
        current_filter=batch.current_filter,
        confirmed=states["confirmed"],
        replaced=replaced,
        kept=states["confirmed"] - replaced,
        skipped=states["skipped"] + states["needs_attention"],
        done=done,
        remaining=states["pending"],
        items=items,
        category_distribution=dict(category),
        winner_sources=dict(sources),
        displayed_sources=dict(displayed_sources),
        selected_sources=dict(selected_sources),
        cross_source_sessions=both,
        strong_pinterest_competition=strong,
    )


def image_path(candidate: PhotoSelectionCandidate, community_id: UUID) -> str | None:
    if candidate.media_asset_id:
        return f"/api/v1/media-assets/{candidate.media_asset_id}/content"
    if candidate.preview_id:
        return f"/api/v1/photo-previews/{candidate.preview_id}/content"
    if candidate.reference_id:
        source = candidate.candidate.get("source_community_id") or str(community_id)
        return f"/api/v1/communities/{source}/references/{candidate.reference_id}/content"
    return None


async def next_alternative(
    session: AsyncSession, selection: PhotoSelectionSession, rejected_rank: int
) -> int | None:
    """Advance within the snapshot without labelling unseen alternatives."""
    options = (
        await session.scalars(
            select(PhotoSelectionCandidate)
            .where(PhotoSelectionCandidate.selection_session_id == selection.id)
            .order_by(PhotoSelectionCandidate.rank)
        )
    ).all()
    available = [
        c.rank
        for c in options
        if c.rank != rejected_rank
        and c.operator_rating != "dislike"
        and image_path(c, selection.community_id)
    ]
    rank = next(
        (rank for rank in available if rank > rejected_rank),
        available[0] if available else None,
    )
    if rank is not None:
        selection.proposed_rank = rank
    return rank


async def item_detail(
    session: AsyncSession, batch: PhotoReviewBatch, position: int
) -> dict[str, object]:
    item = await session.get(PhotoReviewBatchItem, (batch.id, position))
    if not item:
        raise ConflictError("Review item does not exist")
    selection = await get_entity(session, PhotoSelectionSession, item.selection_session_id)
    community = await get_entity(session, Community, selection.community_id)
    submission = await get_entity(session, Submission, item.submission_id)
    options = list(
        (
            await session.scalars(
                select(PhotoSelectionCandidate)
                .where(PhotoSelectionCandidate.selection_session_id == selection.id)
                .order_by(PhotoSelectionCandidate.rank)
            )
        ).all()
    )
    neighbors = []
    for p in [position - 1, position + 1]:
        neighbor = await session.get(PhotoReviewBatchItem, (batch.id, p))
        if neighbor:
            s = await get_entity(session, PhotoSelectionSession, neighbor.selection_session_id)
            c = await session.get(PhotoSelectionCandidate, (s.id, s.chosen_rank or s.proposed_rank))
            if c:
                url = image_path(c, s.community_id)
                if url:
                    neighbors.append(url)
    return dict(
        position=position,
        selection_id=selection.id,
        confirmed_at=selection.confirmed_at,
        state=item.state,
        automatic_rank=item.automatic_rank,
        active_rank=selection.proposed_rank,
        community=community.name or community.domain,
        category=selection.category,
        intent=next(
            (
                c.features.get("context_desired_content")
                for c in options
                if c.rank == selection.proposed_rank
            ),
            None,
        )
        or selection.content_hint,
        attention=submission.photo_attention,
        neighbors=neighbors,
        candidates=[
            dict(
                rank=c.rank,
                provider=c.provider,
                image=image_path(c, community.id),
                rating=c.operator_rating,
                diagnostics=c.features,
            )
            for c in options
        ],
    )
