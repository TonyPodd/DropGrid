"""Descriptive human metrics. No inferred labels, learning or threshold activation."""

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import CommunityDiscoveryEvidence as Evidence
from dropgrid.db.models import CommunityDiscoveryRun as Run
from dropgrid.db.models import CommunityReviewBatchItem as Item
from dropgrid.db.models import CommunityReviewDecision as Decision


def rate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        s: sum(r["decision"] == s for r in rows) for s in ("approved", "rejected", "uncertain")
    }
    decisive = counts["approved"] + counts["rejected"]
    return {
        **counts,
        "reviewed": len(rows),
        "decisive": decisive,
        "approval_rate": counts["approved"] / len(rows) if rows else None,
        "precision": counts["approved"] / decisive if decisive else None,
        "small_sample": decisive < 20,
    }


def score_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    return (
        ">=0.90"
        if value >= 0.9
        else "0.80–0.89"
        if value >= 0.8
        else "0.70–0.79"
        if value >= 0.7
        else "<0.70"
    )


def calculate(
    rows: list[dict[str, Any]], ranked: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    # Exactly one authoritative latest label per canonical candidate, not per revision/reviewer/run.
    latest: dict[str, dict[str, Any]] = {}
    for row in sorted(
        rows, key=lambda r: (r["reviewed_at"], r["reviewer_id"], r.get("revision", 0))
    ):
        latest[row["candidate_id"]] = row
    values = list(latest.values())
    groups: dict[str, dict[str, list[dict[str, Any]]]] = {
        k: defaultdict(list)
        for k in (
            "confidence",
            "cluster",
            "score_bucket",
            "seed_evidence",
            "query_evidence",
            "visual_bucket",
            "region",
            "topic_bucket",
            "activity",
        )
    }
    for row in values:
        snapshot = row["snapshot"]
        c = snapshot["candidate"]
        reason = c["reason"]
        cluster = snapshot.get("cluster", {})
        labels = {
            "confidence": c.get("confidence", "needs_review"),
            "cluster": cluster.get("label")
            or f"{cluster.get('type', 'unknown')}:{cluster.get('value') or 'grid'}",
            "score_bucket": score_bucket(c.get("final_score")),
            "seed_evidence": "multi_seed" if snapshot.get("seed_count", 0) > 1 else "single_seed",
            "query_evidence": "multi_query"
            if len(c.get("found_by_queries", [])) > 1
            else "single_query",
            "visual_bucket": score_bucket(c.get("visual_score")),
            "region": reason.get("region_status", "unverified"),
            "topic_bucket": score_bucket(c.get("topic_score")),
            "activity": reason.get("activity", {}).get("state", "unknown"),
        }
        for key, label in labels.items():
            groups[key][label].append(row)
    peer: dict[str, dict[str, str]] = defaultdict(dict)
    for row in sorted(rows, key=lambda r: (r["reviewed_at"], r.get("revision", 0))):
        # Revisions overwrite the same reviewer/candidate vote; no duplicate agreement votes.
        peer[row["candidate_id"]][row["reviewer_id"]] = row["decision"]
    pairs = [list(v.values()) for v in peer.values() if len(v) == 2]
    agreement = sum(a == b for a, b in pairs) / len(pairs) if pairs else None
    at_k: dict[str, dict[str, float | None]] = {}
    for run_id, order in (ranked or {}).items():
        at_k[run_id] = {}
        for k in (5, 10, 20):
            top = order[:k]
            top_labels = [latest.get(id) for id in top]
            enough = len(top) == k and all(
                r and r["decision"] in {"approved", "rejected"} for r in top_labels
            )
            at_k[run_id][str(k)] = (
                sum(r["decision"] == "approved" for r in top_labels if r) / k if enough else None
            )
    thresholds = []
    for cutoff in (0.90, 0.85, 0.80):
        eligible = [r for r in values if r["snapshot"]["candidate"]["final_score"] >= cutoff]
        counts = rate(eligible)
        thresholds.append(
            {
                "threshold": cutoff,
                **counts,
                "would_add_reviewed": len(eligible),
                "would_be_wrong": counts["rejected"],
                "unresolved": counts["uncertain"],
                "auto_add_enabled": False,
            }
        )
    return {
        "overall": rate(values),
        "by": {k: {label: rate(v) for label, v in sorted(g.items())} for k, g in groups.items()},
        "agreement": {"overlapping_candidates": len(pairs), "rate": agreement},
        "precision_at_k": at_k,
        "thresholds": thresholds,
        "training": {
            "positive": sum(r["decision"] == "approved" for r in values),
            "negative": sum(r["decision"] == "rejected" for r in values),
            "uncertain_excluded": sum(r["decision"] == "uncertain" for r in values),
            "enabled": False,
        },
    }


async def quality(db: AsyncSession, grid_id: UUID, batch_id: UUID | None = None) -> dict[str, Any]:
    query = select(Decision, Item.candidate_id).join(
        Item, (Item.batch_id == Decision.batch_id) & (Item.position == Decision.position)
    )
    from dropgrid.db.models import CommunityReviewBatch as Batch

    query = query.join(Batch, Batch.id == Item.batch_id).where(Batch.grid_id == grid_id)
    if batch_id:
        query = query.where(Item.batch_id == batch_id)
    data = [
        {
            "candidate_id": str(cid),
            "reviewer_id": str(d.reviewer_id),
            "decision": d.decision,
            "snapshot": d.snapshot,
            "reviewed_at": d.reviewed_at.isoformat(),
            "revision": d.revision,
        }
        for d, cid in (await db.execute(query)).all()
    ]
    runs = (
        await db.scalars(
            select(Run).where(Run.grid_id == grid_id, Run.state == "ready").order_by(Run.started_at)
        )
    ).all()
    ranked = {}
    for run in runs:
        rows = (
            await db.scalars(
                select(Evidence).where(
                    Evidence.run_id == run.id, Evidence.analysis_snapshot.is_not(None)
                )
            )
        ).all()
        regional = any(q.get("region_key") for q in run.queries)
        eligible = [
            e for e in rows if e.analysis_snapshot and e.analysis_snapshot.get("status") == "new"
        ]
        eligible.sort(
            key=lambda e: (
                0 if not regional or (e.analysis_snapshot or {}).get("region_score") == 1 else 1,
                -(e.analysis_snapshot or {}).get("final_score", 0),
                (e.analysis_snapshot or {}).get("vk_group_id", 0),
            )
        )
        ranked[str(run.id)] = [str(e.candidate_id) for e in eligible]
    result = calculate(data, ranked)
    result["reviewers"] = {
        id: len({r["candidate_id"] for r in data if r["reviewer_id"] == id})
        for id in sorted({r["reviewer_id"] for r in data})
    }
    return result
