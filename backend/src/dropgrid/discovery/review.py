"""Human labels and explicit cursors; never invokes VK or campaign preparation."""

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import (
    Community,
    PhotoReviewer,
    utcnow,
)
from dropgrid.db.models import (
    CommunityDiscoveryCandidate as Candidate,
)
from dropgrid.db.models import (
    CommunityDiscoveryEvidence as Evidence,
)
from dropgrid.db.models import (
    CommunityDiscoveryRun as Run,
)
from dropgrid.db.models import (
    CommunityReviewBatch as Batch,
)
from dropgrid.db.models import (
    CommunityReviewBatchItem as Item,
)
from dropgrid.db.models import (
    CommunityReviewCursor as Cursor,
)
from dropgrid.db.models import (
    CommunityReviewDecision as Decision,
)
from dropgrid.db.models import (
    CommunityReviewEvent as Event,
)
from dropgrid.discovery.service import candidate_read
from dropgrid.services.catalog import ConflictError, get_entity

MODES = {"pending", "approved", "rejected", "uncertain", "all"}
REASONS = {
    "wrong_topic",
    "wrong_region",
    "dead_or_inactive",
    "too_broad",
    "commercial_or_irrelevant",
    "duplicate",
    "other",
}


def version(snapshot: dict[str, Any]) -> str:
    stable = dict(snapshot)
    candidate = dict(stable["candidate"])
    for field in (
        "status",
        "classification",
        "human_status",
        "human_reviewed_at",
        "human_reviewer_id",
        "approved_at",
        "approved_by",
    ):
        candidate.pop(field, None)
    candidate["reason"] = {k: v for k, v in candidate["reason"].items() if k != "decision_by"}
    stable["candidate"] = candidate
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


async def create_batch(
    db: AsyncSession,
    grid_id: UUID,
    name: str,
    reviewer: UUID | None,
    candidate_ids: list[UUID] | None = None,
) -> Batch:
    from dropgrid.db.models import Grid

    await get_entity(db, Grid, grid_id)
    query = select(Candidate).where(
        Candidate.grid_id == grid_id,
        Candidate.status == "new",
        Candidate.analysis_state == "ready",
        Candidate.human_status.is_(None),
    )
    if candidate_ids is not None:
        query = query.where(Candidate.id.in_(candidate_ids))
    candidates = (
        await db.scalars(query.order_by(Candidate.final_score.desc(), Candidate.vk_group_id))
    ).all()
    if not candidates or len(candidates) > 250:
        raise ConflictError("A review batch must contain 1 to 250 unreviewed candidates")
    if candidate_ids is not None and set(candidate_ids) != {c.id for c in candidates}:
        raise ConflictError("Some requested candidates are not eligible for this Grid")
    batch = Batch(grid_id=grid_id, name=name, created_by=reviewer)
    db.add(batch)
    await db.flush()
    for position, candidate in enumerate(candidates):
        run_id = await db.scalar(
            select(Evidence.run_id)
            .join(Run, Run.id == Evidence.run_id)
            .where(Evidence.candidate_id == candidate.id, Run.state == "ready")
            .order_by(Run.finished_at.desc(), Run.id)
            .limit(1)
        )
        db.add(Item(batch_id=batch.id, position=position, candidate_id=candidate.id, run_id=run_id))
    await db.flush()
    return batch


async def positions(db: AsyncSession, batch_id: UUID, reviewer: UUID, mode: str) -> list[int]:
    items = (
        await db.scalars(
            select(Item.position).where(Item.batch_id == batch_id).order_by(Item.position)
        )
    ).all()
    labels = {
        d.position: d.decision
        for d in (
            await db.scalars(
                select(Decision).where(
                    Decision.batch_id == batch_id, Decision.reviewer_id == reviewer
                )
            )
        ).all()
    }
    return [
        p
        for p in items
        if mode == "all" or (p not in labels if mode == "pending" else labels.get(p) == mode)
    ]


async def summary(db: AsyncSession, batch: Batch, reviewer: UUID | None) -> dict[str, Any]:
    items = (
        await db.scalars(select(Item).where(Item.batch_id == batch.id).order_by(Item.position))
    ).all()
    decisions = (await db.scalars(select(Decision).where(Decision.batch_id == batch.id))).all()
    own = [d for d in decisions if d.reviewer_id == reviewer]
    cursor = await db.get(Cursor, (batch.id, reviewer)) if reviewer else None
    names = {r.id: r.display_name for r in (await db.scalars(select(PhotoReviewer))).all()}
    return {
        "id": str(batch.id),
        "grid_id": str(batch.grid_id),
        "name": batch.name,
        "state": batch.state,
        "total": len(items),
        "reviewed": len({d.position for d in decisions}),
        "reviewed_by_you": len(own),
        "counts": {
            state: sum(d.decision == state for d in own)
            for state in ("approved", "rejected", "uncertain")
        },
        "reviewer_id": str(reviewer) if reviewer else None,
        "reviewer_name": names.get(reviewer, "") if reviewer else "",
        "reviewers": [
            {"id": str(id), "name": name, "reviewed": sum(d.reviewer_id == id for d in decisions)}
            for id, name in names.items()
        ],
        "position": cursor.position if cursor else 0,
        "filter": cursor.mode if cursor else "pending",
        "url": f"/discovery/review/{batch.id}",
    }


async def analysis(db: AsyncSession, item: Item, candidate: Candidate) -> dict[str, Any]:
    evidence = await db.get(Evidence, (item.run_id, candidate.id)) if item.run_id else None
    frozen = evidence.analysis_snapshot if evidence else None
    value = json.loads(json.dumps(candidate_read(candidate), default=str))
    if frozen:
        value.update(
            {k: v for k, v in frozen.items() if k not in {"snapshot_source", "preview_cache_keys"}}
        )
    from dropgrid.photos.regions import grid_region

    context = grid_region(None, value.get("suggested_region_key"))
    target = grid_region(None, value.get("reason", {}).get("target_region_key"))
    value["region"] = context.as_dict() if context else None
    seed_ids = value.get("seed_community_ids", [])
    seeds = (
        await db.scalars(
            select(Community)
            .where(Community.id.in_([UUID(id) for id in seed_ids[:20]]))
            .order_by(Community.id)
        )
    ).all()
    run = await db.get(Run, item.run_id) if item.run_id else None
    # Only labels retain an immutable copy; membership references frozen Run evidence.
    cluster_label = (
        run.scope_value
        if run and run.scope_type == "category"
        else " · ".join(
            dict.fromkeys(str(q["topic"]) for q in (run.queries if run else []) if q.get("topic"))
        )
    )
    if run and run.scope_type == "region":
        region = grid_region(None, run.scope_value)
        cluster_label = f"{region.display_name if region else run.scope_value} · {cluster_label}"
    return {
        "candidate": value,
        "target_region": target.as_dict() if target else None,
        "preview_cache_keys": frozen.get("preview_cache_keys", list(candidate.previews))
        if frozen
        else list(candidate.previews),
        "seed_count": len(seed_ids),
        "seeds": [
            {"id": str(c.id), "name": c.name or c.domain, "url": "https://vk.com/" + c.domain}
            for c in seeds
        ],
        "run_id": str(item.run_id) if item.run_id else None,
        "cluster": {"type": run.scope_type, "value": run.scope_value, "label": cluster_label}
        if run
        else {"type": "unknown", "value": None},
    }


async def detail(db: AsyncSession, batch: Batch, position: int, reviewer: UUID) -> dict[str, Any]:
    item = await get_entity(db, Item, (batch.id, position))
    candidate = await get_entity(db, Candidate, item.candidate_id)
    label = await db.get(Decision, (batch.id, position, reviewer))
    snapshot = label.snapshot if label else await analysis(db, item, candidate)
    public = {k: v for k, v in snapshot.items() if k != "preview_cache_keys"}
    value = dict(public["candidate"])
    value["previews"] = [
        f"/api/v1/discovery/review-batches/{batch.id}/items/{position}/photos/{i}?reviewer_id={reviewer}"
        for i in range(min(12, len(snapshot.get("preview_cache_keys", []))))
    ]
    return {
        **public,
        "candidate": value,
        "position": position,
        "version": version(snapshot),
        "decision": label.decision if label else None,
        "reason": label.reason if label else None,
        "revision": label.revision if label else 0,
        "reviewed_at": label.reviewed_at.isoformat() if label else None,
        "human_status": candidate.human_status,
        "grid_added": candidate.status == "approved",
    }


async def save_cursor(
    db: AsyncSession, batch: Batch, reviewer: UUID, position: int | None, mode: str
) -> Cursor:
    if mode not in MODES:
        raise ConflictError("Invalid review filter")
    count = len((await db.scalars(select(Item.position).where(Item.batch_id == batch.id))).all())
    if position is None:
        choices = await positions(db, batch.id, reviewer, mode)
        position = choices[0] if choices else count
    if not 0 <= position <= count:
        raise ConflictError("Review position outside batch")
    row = await db.get(Cursor, (batch.id, reviewer))
    if row is None:
        row = Cursor(batch_id=batch.id, reviewer_id=reviewer, position=position, mode=mode)
        db.add(row)
    else:
        row.position, row.mode = position, mode
    await db.flush()
    return row


async def decide(
    db: AsyncSession,
    batch: Batch,
    position: int,
    reviewer: UUID,
    decision: str,
    reason: str | None,
    seen_version: str,
    expected_revision: int,
) -> dict[str, Any]:
    if batch.state != "open" or decision not in {"approved", "rejected", "uncertain"}:
        raise ConflictError("Community review is closed or decision invalid")
    if reason and (decision != "rejected" or reason not in REASONS):
        raise ConflictError("Invalid rejection reason")
    item = await get_entity(db, Item, (batch.id, position))
    candidate = await db.get(
        Candidate, item.candidate_id, with_for_update=True, populate_existing=True
    )
    assert candidate
    label = await db.get(
        Decision, (batch.id, position, reviewer), with_for_update=True, populate_existing=True
    )
    snapshot = label.snapshot if label else await analysis(db, item, candidate)
    if expected_revision != (label.revision if label else 0) or seen_version != version(snapshot):
        raise ConflictError("Review analysis or decision changed; reload before deciding")
    now = utcnow()
    revision = label.revision + 1 if label else 1
    if label is None:
        label = Decision(
            batch_id=batch.id,
            position=position,
            reviewer_id=reviewer,
            decision=decision,
            reason=reason,
            snapshot=snapshot,
            reviewed_at=now,
            revision=revision,
        )
        db.add(label)
    else:
        label.decision, label.reason, label.reviewed_at, label.revision = (
            decision,
            reason,
            now,
            revision,
        )
    db.add(
        Event(
            batch_id=batch.id,
            position=position,
            reviewer_id=reviewer,
            decision=decision,
            reason=reason,
            snapshot=snapshot,
            reviewed_at=now,
            revision=revision,
        )
    )
    candidate.human_status, candidate.human_reviewed_at, candidate.human_reviewer_id = (
        decision,
        now,
        reviewer,
    )
    if (
        candidate.status != "approved"
    ):  # Membership is a distinct state; revisions never remove a Grid row.
        candidate.status = "rejected" if decision == "rejected" else "new"
        candidate.classification = "previously_rejected" if decision == "rejected" else "new"
    await db.flush()
    cursor = await db.get(Cursor, (batch.id, reviewer))
    mode = cursor.mode if cursor else "pending"
    choices = await positions(db, batch.id, reviewer, mode)
    remaining = [p for p in choices if p != position]
    next_position = next(
        (p for p in remaining if p > position), remaining[0] if remaining else None
    )
    count = len((await db.scalars(select(Item.position).where(Item.batch_id == batch.id))).all())
    await save_cursor(
        db, batch, reviewer, next_position if next_position is not None else count, mode
    )
    return {
        "saved": True,
        "next_position": next_position,
        "batch": await summary(db, batch, reviewer),
    }
