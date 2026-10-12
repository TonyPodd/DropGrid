"""Human community labels never imply membership, external writes or training."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select
from test_community_discovery import fixture

from dropgrid.db.models import (
    CommunityDiscoveryCandidate as Candidate,
)
from dropgrid.db.models import (
    CommunityDiscoveryEvidence as Evidence,
)
from dropgrid.db.models import (
    CommunityReviewBatchItem as Item,
)
from dropgrid.db.models import (
    CommunityReviewDecision as Decision,
)
from dropgrid.db.models import (
    CommunityReviewEvent as Event,
)
from dropgrid.db.models import (
    GridCommunity,
    PhotoReviewer,
    Submission,
)
from dropgrid.discovery import review
from dropgrid.discovery.metrics import calculate
from dropgrid.discovery.service import RunInput
from dropgrid.services.catalog import ConflictError


async def boot(sessions, tmp_path):
    service, gid, aid, _, rid, methods = await fixture(sessions, tmp_path)
    await service.enqueue(RunInput(grid_id=gid, account_id=aid))
    for _ in range(6):
        await service.tick()
    async with sessions() as db, db.begin():
        batch = await review.create_batch(db, gid, "Discovery validation", rid)
        candidate = await db.scalar(select(Candidate).where(Candidate.vk_group_id == 200))
        return service, gid, rid, batch.id, candidate.id, methods


@pytest.mark.integration
@pytest.mark.parametrize(
    "decision,reason", [("approved", None), ("rejected", "wrong_region"), ("uncertain", None)]
)
async def test_decision_revision_snapshot_cursor_and_no_membership(
    sessions, tmp_path, decision, reason
):
    _, gid, rid, bid, cid, methods = await boot(sessions, tmp_path)
    async with sessions() as db, db.begin():
        from dropgrid.db.models import CommunityReviewBatch

        batch = await db.get(CommunityReviewBatch, bid)
        shown = await review.detail(db, batch, 0, rid)
        before = shown["candidate"]["final_score"]
        result = await review.decide(db, batch, 0, rid, decision, reason, shown["version"], 0)
        assert result["next_position"] is None and result["batch"]["position"] == 1
        label = await db.get(Decision, (bid, 0, rid))
        assert label.reason == reason and label.snapshot["candidate"]["final_score"] == before
        c = await db.get(Candidate, cid)
        assert c.human_status == decision
        assert c.status == ("rejected" if decision == "rejected" else "new")
        assert await db.scalar(select(func.count()).select_from(GridCommunity)) == 1
        assert await db.scalar(select(func.count()).select_from(Submission)) == 0
        c.final_score = 0.123  # later analysis must not rewrite seen training data
        await db.flush()
        again = await review.detail(db, batch, 0, rid)
        assert again["candidate"]["final_score"] == before and again["version"] == shown["version"]
        await review.decide(db, batch, 0, rid, "approved", None, again["version"], 1)
        assert await db.scalar(select(func.count()).select_from(Decision)) == 1
        assert await db.scalar(select(func.count()).select_from(Event)) == 2
        assert label.revision == 2
        assert label.snapshot["candidate"]["final_score"] == before
        with pytest.raises(ConflictError, match="changed"):
            await review.decide(db, batch, 0, rid, "approved", None, again["version"], 1)
    assert set(methods) <= {"users.get", "groups.search", "groups.getById", "wall.get"}


@pytest.mark.integration
async def test_api_filters_resume_attribution_and_explicit_grid_provenance(
    client, sessions, tmp_path
):
    service, gid, rid, bid, cid, _ = await boot(sessions, tmp_path)
    client._transport.app.state.discovery = service
    prefix = f"/api/v1/discovery/review-batches/{bid}"
    d = (await client.get(f"{prefix}/items/0?reviewer_id={rid}")).json()
    assert d["candidate"]["url"] == "https://vk.com/good" and d["seed_count"] == 1
    denied = await client.post(
        f"/api/v1/discovery/candidates/{cid}/decision",
        json={"action": "approve", "reviewer_id": str(rid)},
    )
    assert denied.status_code == 409
    r = await client.post(
        f"{prefix}/items/0/decision",
        json={
            "reviewer_id": str(rid),
            "decision": "approved",
            "seen_version": d["version"],
            "expected_revision": 0,
        },
    )
    assert r.status_code == 200 and r.json()["batch"]["reviewed_by_you"] == 1
    cursor = await client.put(
        f"{prefix}/cursor", json={"reviewer_id": str(rid), "position": 0, "mode": "approved"}
    )
    assert cursor.status_code == 200
    resumed = (await client.get(f"{prefix}?reviewer_id={rid}")).json()
    assert resumed["position"] == 0 and resumed["filter"] == "approved"
    candidates = (
        await client.get(f"/api/v1/discovery/candidates?grid_id={gid}&status=human_approved")
    ).json()
    assert candidates["counters"]["human_approved"] == 1 and candidates["counters"]["added"] == 0
    before = (await client.get(f"/api/v1/discovery/quality?grid_id={gid}")).json()
    assert before["overall"]["precision"] == 1 and before["training"]["positive"] == 1
    r = await client.post(
        f"/api/v1/discovery/candidates/{cid}/decision",
        json={
            "action": "approve",
            "reviewer_id": str(rid),
            "category": "124 регион",
            "region_key": "krasnoyarsk_krai",
        },
    )
    assert r.status_code == 200
    async with sessions() as db:
        gc = (
            await db.scalars(
                select(GridCommunity).where(
                    GridCommunity.grid_id == gid, GridCommunity.discovery_provenance.is_not(None)
                )
            )
        ).one()
        assert gc.discovery_provenance["human_reviewer"] == str(rid)
        assert gc.discovery_provenance["human_decision_at"]
        assert gc.discovery_provenance["human_review_batch_id"] == str(bid)
        assert gc.discovery_provenance["suggested_category"] == "24 регион"
        assert gc.discovery_provenance["final_category"] == "124 регион"
        assert await db.scalar(select(func.count()).select_from(Submission)) == 0


@pytest.mark.integration
async def test_two_reviewers_have_independent_positions_and_labels(sessions, tmp_path):
    _, _, tima, bid, _, _ = await boot(sessions, tmp_path)
    async with sessions() as db, db.begin():
        from dropgrid.db.models import CommunityReviewBatch

        b = await db.get(CommunityReviewBatch, bid)
        tony = PhotoReviewer(display_name="Tony")
        db.add(tony)
        await db.flush()
        d = await review.detail(db, b, 0, tima)
        await review.decide(db, b, 0, tima, "uncertain", None, d["version"], 0)
        own = await review.summary(db, b, tony.id)
        assert own["reviewed_by_you"] == 0 and own["position"] == 0
        d = await review.detail(db, b, 0, tony.id)
        await review.decide(db, b, 0, tony.id, "rejected", "duplicate", d["version"], 0)
        assert await db.scalar(select(func.count()).select_from(Decision)) == 2
        assert (await review.summary(db, b, tima))["counts"]["uncertain"] == 1
        assert (await review.summary(db, b, tony.id))["counts"]["rejected"] == 1


@pytest.mark.integration
async def test_snapshot_versions_detect_reanalysis_and_rejections_stay_suppressed(
    sessions, tmp_path
):
    service, gid, rid, bid, cid, methods = await boot(sessions, tmp_path)
    async with sessions() as db, db.begin():
        from dropgrid.db.models import CommunityReviewBatch

        b = await db.get(CommunityReviewBatch, bid)
        shown = await review.detail(db, b, 0, rid)
        item = await db.get(Item, (bid, 0))
        evidence = await db.get(Evidence, (item.run_id, cid))
        evidence.analysis_snapshot = {**evidence.analysis_snapshot, "final_score": 0.42}
        await db.flush()
        with pytest.raises(ConflictError, match="changed"):
            await review.decide(db, b, 0, rid, "approved", None, shown["version"], 0)
        shown = await review.detail(db, b, 0, rid)
        await review.decide(db, b, 0, rid, "rejected", "wrong_topic", shown["version"], 0)
        from dropgrid.db.models import Account

        account = (await db.scalars(select(Account))).one()
    async with service.client:
        await service.enqueue(RunInput(grid_id=gid, account_id=account.id))
        for _ in range(6):
            await service.tick()
    async with sessions() as db:
        assert (await db.get(Candidate, cid)).status == "rejected"
        assert await db.scalar(select(func.count()).select_from(Decision)) == 1
        assert await db.scalar(select(func.count()).select_from(Item)) == 1
        assert all(
            e.analysis_snapshot is not None for e in (await db.scalars(select(Evidence))).all()
        )
    assert set(methods) <= {"users.get", "groups.search", "groups.getById", "wall.get"}


def row(id, decision="approved", score=0.92, reviewer="Tony", at="1"):
    return {
        "candidate_id": str(id),
        "reviewer_id": reviewer,
        "reviewed_at": at,
        "decision": decision,
        "snapshot": {
            "seed_count": 2,
            "cluster": {"type": "region", "value": "buryatia"},
            "candidate": {
                "confidence": "high",
                "final_score": score,
                "visual_score": 0.87,
                "topic_score": 1,
                "found_by_queries": ["one", "two"],
                "reason": {"region_status": "same_region", "activity": {"state": "active"}},
            },
        },
    }


def test_metrics_no_labels_no_fake_precision_and_uncertain_not_negative():
    value = calculate([], {"run": [str(i) for i in range(20)]})
    assert value["overall"]["precision"] is None and value["precision_at_k"]["run"]["10"] is None
    rows = [row(i, "approved" if i % 2 == 0 else "rejected") for i in range(10)]
    result = calculate(rows, {"run": [str(i) for i in range(20)]})
    assert result["overall"]["precision"] == 0.5
    assert result["precision_at_k"]["run"] == {"5": 0.6, "10": 0.5, "20": None}
    assert result["thresholds"][0]["would_be_wrong"] == 5
    assert result["by"]["region"]["same_region"]["reviewed"] == 10
    changed = deepcopy(rows[0])
    changed.update(decision="uncertain", reviewed_at="2")
    result = calculate([*rows, changed, row(0, "approved", reviewer="Tima", at="3")])
    assert result["overall"]["reviewed"] == 10 and result["agreement"]["rate"] == 0
    assert result["training"]["enabled"] is False
    uncertain = calculate([row(0, "uncertain")])
    assert uncertain["training"]["negative"] == 0 and uncertain["overall"]["precision"] is None


@pytest.mark.integration
async def test_create_batch_api_trusted_identity_and_no_labels(client, sessions, tmp_path):
    from fastapi import HTTPException
    from starlette.requests import Request

    from dropgrid.discovery.review_routes import actor_id

    service, gid, rid, _, _, _ = await boot(sessions, tmp_path)
    client._transport.app.state.discovery = service
    response = await client.post(
        "/api/v1/discovery/review-batches",
        json={"grid_id": str(gid), "name": "Fresh review", "reviewer_id": str(rid)},
    )
    assert response.status_code == 201
    assert response.json()["total"] == 1 and response.json()["reviewed"] == 0
    async with sessions() as db, db.begin():
        reviewer = await db.get(PhotoReviewer, rid)
        reviewer.display_name = "Tima"
        tony = PhotoReviewer(display_name="Tony")
        db.add(tony)
        await db.flush()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [],
                "state": {"remote_user": "tima"},
            }
        )
        assert await actor_id(request, db, None) == rid
        with pytest.raises(HTTPException) as denied:
            await actor_id(request, db, tony.id)
        assert denied.value.status_code == 403
        assert await db.scalar(select(func.count()).select_from(Decision)) == 0


def test_cache_pruning_diagnostics_isolated_and_bounded(tmp_path):
    import os
    from datetime import timedelta

    from dropgrid.db.models import utcnow
    from dropgrid.discovery.cache import inspect_cache, prune

    root = tmp_path / "discovery"
    root.mkdir()
    folder = root / "aa"
    folder.mkdir()
    stale = folder / "old.jpg"
    stale.write_bytes(b"old")
    os.utime(stale, ((utcnow() - timedelta(days=31)).timestamp(),) * 2)
    fresh = folder / "new.jpg"
    fresh.write_bytes(b"new")
    production = tmp_path / "production.jpg"
    production.write_bytes(b"keep")
    assert inspect_cache(root)["entries"] == 2
    assert prune(root) == 1 and production.read_bytes() == b"keep"
    data = inspect_cache(root)
    assert data["size"] == 3 and data["entries"] == 1 and data["oldest"]
    assert prune(root) == 0


@pytest.mark.parametrize(
    "score,bucket",
    [(0.9, ">=0.90"), (0.89, "0.80–0.89"), (0.79, "0.70–0.79"), (0.69, "<0.70"), (None, "unknown")],
)
def test_score_buckets_and_top_k_require_original_top_labels(score, bucket):
    from dropgrid.discovery.metrics import score_bucket

    assert score_bucket(score) == bucket
    result = calculate([row(i) for i in range(1, 6)], {"run": [str(i) for i in range(20)]})
    assert result["precision_at_k"]["run"]["5"] is None


@pytest.mark.integration
async def test_unreviewed_cohort_uses_originating_run_and_concurrent_reviewers(sessions, tmp_path):
    _, _, rid, bid, cid, _ = await boot(sessions, tmp_path)
    from dropgrid.db.models import CommunityReviewBatch

    async with sessions() as db, db.begin():
        b = await db.get(CommunityReviewBatch, bid)
        other = PhotoReviewer(display_name="Tony")
        db.add(other)
        await db.flush()
        d = await review.detail(db, b, 0, rid)
        peer = await review.detail(db, b, 0, other.id)
        candidate = await db.get(Candidate, cid)
        candidate.final_score = 0.01
        candidate.suggested_category = "Changed by a later run"
        await db.flush()
        stable = await review.detail(db, b, 0, rid)
        assert (
            stable["version"] == d["version"]
            and stable["candidate"]["final_score"] == d["candidate"]["final_score"]
        )
        await review.decide(db, b, 0, rid, "approved", None, d["version"], 0)
        await review.decide(db, b, 0, other.id, "rejected", "other", peer["version"], 0)
        assert candidate.human_status == "rejected"


def test_open_review_previews_have_eviction_priority_but_never_bypass_cap_or_ttl(tmp_path):
    import os
    from datetime import timedelta

    from dropgrid.db.models import utcnow
    from dropgrid.discovery.cache import inspect_cache, prune

    root = tmp_path / "discovery"
    folder = root / "aa"
    folder.mkdir(parents=True)
    pinned = folder / "pinned.jpg"
    ordinary = folder / "ordinary.jpg"
    for p in (pinned, ordinary):
        with p.open("wb") as f:
            f.truncate(200 * 1024 * 1024)
    os.utime(pinned, ((utcnow() - timedelta(days=2)).timestamp(),) * 2)
    protected = frozenset({"aa/pinned.jpg"})
    assert prune(root, protected) == 1 and pinned.exists() and not ordinary.exists()
    assert inspect_cache(root)["size"] <= 300 * 1024 * 1024
    os.utime(pinned, ((utcnow() - timedelta(days=31)).timestamp(),) * 2)
    assert prune(root, protected) == 1 and not pinned.exists()
