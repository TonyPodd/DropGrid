from collections import Counter, defaultdict
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_photo_integration import prepared
from test_readiness_workflow import archive_fixture

from dropgrid.db.models import (
    Campaign,
    Community,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
)
from dropgrid.photos.domain import PhotoError
from dropgrid.photos.failures import reason
from dropgrid.photos.pool import materialize_archive
from dropgrid.photos.review import display_set
from dropgrid.photos.validation import stratify


def test_source_balanced_display_keeps_winner_without_score_bonus():
    options = [SimpleNamespace(source="vk_category_archive", identity=i) for i in range(20)]
    options += [
        SimpleNamespace(source=p, identity=20 + i)
        for i, p in enumerate(["pinterest", "library", "vk_archive", "pixabay"])
    ]
    chosen = options[5]
    display = display_set(options, chosen)
    assert len(display) == 12 and chosen in display
    assert {i.source for i in display} == {
        "pinterest",
        "library",
        "vk_archive",
        "pixabay",
        "vk_category_archive",
    }
    assert len(options) == 24 and options[5] is chosen


def test_stratification_deterministic_150_covers_axes_and_competition():
    rows = []
    candidates = defaultdict(list)
    for i in range(200):
        s = PhotoSelectionSession(
            id=UUID(int=i + 1),
            community_id=UUID(int=i + 1),
            category=f"category{i % 20}",
            content_hint="hint" if i % 3 else None,
            proposed_rank=1,
        )
        rows.append((s, Submission(photo_attention=["small_candidate_pool"] if i % 4 else [])))
        providers = (
            ["vk_category_archive", "pinterest"]
            if i < 10
            else ["library"]
            if i % 3
            else ["pinterest"]
        )
        candidates[s.id] = [
            PhotoSelectionCandidate(
                rank=j + 1, provider=p, features={"final_score": 1 - j * (0.01 if i % 2 else 0.2)}
            )
            for j, p in enumerate(providers)
        ]
    first = stratify(rows, candidates, 150)
    assert len(first) == 150 and first == stratify(rows, candidates, 150)
    assert len({s.category for s in first}) == 20
    assert {UUID(int=i + 1) for i in range(10)} <= {s.id for s in first}
    assert Counter(candidates[s.id][0].provider for s in first)["library"] > 0


@pytest.mark.parametrize(
    "pool,warnings,cooldown,reuse,expected",
    [
        (0, ["pinterest_search_unavailable"], 0, 0, "no_candidates"),
        (2, [], 2, 0, "cooldown_exhausted"),
        (2, [], 0, 2, "dedup_exhausted"),
        (2, ["archive_storage_unavailable"], 0, 0, "storage_error"),
        (2, ["download_failed"], 0, 0, "download_error"),
        (2, ["category_archive_context_changed"], 0, 0, "category_archive_error"),
        (2, ["archive_candidate_unavailable"], 0, 0, "materialization_failed"),
        (2, ["references_unavailable"], 0, 0, "no_references"),
        (2, ["pinterest_search_unavailable"], 0, 0, "pinterest_unavailable"),
        (2, [], 0, 0, "other"),
    ],
)
def test_safe_failure_reasons(pool, warnings, cooldown, reuse, expected):
    assert reason(pool, warnings, cooldown, reuse) == expected


@pytest.mark.integration
async def batch_fixture(client, sessions, tmp_path):
    cid, sid, target, ref, _, _, planner, item, http = await archive_fixture(sessions, tmp_path)
    client._transport.app.state.photo_engine.planner = planner
    asset, _ = await materialize_archive(planner.archive, planner, item, "Cats", target_id=target)
    async with sessions() as db, db.begin():
        row = await db.get(Submission, sid)
        row.media_asset_id = asset.id
        s = PhotoSelectionSession(
            campaign_id=cid,
            submission_id=sid,
            community_id=target,
            category="Cats",
            proposed_rank=1,
            baseline_rank=1,
        )
        db.add(s)
        await db.flush()
        for rank in [1, 2, 3]:
            db.add(
                PhotoSelectionCandidate(
                    selection_session_id=s.id,
                    rank=rank,
                    provider="library",
                    source_identity=str(rank),
                    media_asset_id=asset.id,
                    features={},
                    candidate={},
                )
            )
    t = (await client.post("/api/v1/photo-reviewers", json={"display_name": "Tima"})).json()["id"]
    b = (
        await client.post(
            f"/api/v1/campaigns/{cid}/review-batches", json={"target_count": 1, "reviewer_id": t}
        )
    ).json()
    return cid, sid, s.id, t, b, planner, http


@pytest.mark.integration
@pytest.mark.parametrize("domain", ["izh_sueta", "public242100737", ""])
async def test_review_item_community_url_uses_domain_not_display_name(
    client, sessions, tmp_path, domain
):
    _, _, selection_id, _, batch, _, _ = await batch_fixture(client, sessions, tmp_path)
    async with sessions() as db, db.begin():
        selection = await db.get(PhotoSelectionSession, selection_id)
        community = await db.get(Community, selection.community_id)
        community.domain = domain
        community.name = "Display name, not a VK domain"
    response = await client.get(f"/api/v1/review-batches/{batch['id']}/items/0")
    assert response.status_code == 200
    detail = response.json()
    assert detail["community"] == "Display name, not a VK domain"
    assert detail["community_domain"] == (domain or None)
    assert detail["community_url"] == (f"https://vk.com/{domain}" if domain else None)


@pytest.mark.integration
async def test_confirm_edit_resume_reviewer_labels_without_duplicate_decisions(
    client, sessions, tmp_path
):
    cid, sid, selection_id, t, b, planner, http = await batch_fixture(client, sessions, tmp_path)
    path = f"/api/v1/review-batches/{b['id']}/items/0/action"
    data = {
        "selection_id": str(selection_id),
        "reviewer_id": t,
        "action": "confirm",
        "rank": 1,
        "shown_ranks": [1, 2],
    }
    response = await client.post(path, json=data)
    assert response.status_code == 200, response.text
    assert response.json()["confirmed"] == 1 and response.json()["remaining"] == 0
    async with sessions() as db:
        decision = await db.get(PhotoSelectionSession, selection_id)
        assert str(decision.reviewer_id) == t and decision.confirmed_at
        assert (await db.get(PhotoSelectionCandidate, (selection_id, 1))).selected
        assert (await db.get(PhotoSelectionCandidate, (selection_id, 2))).displayed
        assert not (await db.get(PhotoSelectionCandidate, (selection_id, 3))).displayed
    data.update(
        rank=2,
        expected_confirmed_at=(
            await client.get(f"/api/v1/review-batches/{b['id']}/items/0")
        ).json()["confirmed_at"],
    )
    changed = await client.post(path, json=data)
    assert changed.status_code == 200 and changed.json()["replaced"] == 1
    async with sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(PhotoSelectionSession)
                .where(PhotoSelectionSession.confirmed_at.is_not(None))
            )
            == 1
        )
        assert not (await db.get(PhotoSelectionCandidate, (selection_id, 1))).selected
        assert (await db.get(PhotoSelectionCandidate, (selection_id, 2))).selected
    status = (await client.get("/api/v1/photo-ranking")).json()
    assert status["choices"] == 1 and status["reviewers"] == {"Tima": 1}
    cursor = await client.put(
        f"/api/v1/review-batches/{b['id']}/cursor",
        json={"position": 0, "mode": "all", "reviewer_id": t},
    )
    assert cursor.status_code == 200
    recovered = (await client.get(f"/api/v1/review-batches/{b['id']}")).json()
    assert recovered["current_filter"] == "all" and recovered["reviewer_id"] == t
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        campaign.status = "running"
    assert (await client.post(path, json=data)).status_code == 409
    await http.aclose()


@pytest.mark.integration
async def test_skip_weak_evidence_stale_snapshot_and_no_hidden_labels(client, sessions, tmp_path):
    _, _, selection_id, t, b, _, http = await batch_fixture(client, sessions, tmp_path)
    path = f"/api/v1/review-batches/{b['id']}/items/0/action"
    data = {
        "selection_id": str(uuid4()),
        "reviewer_id": t,
        "action": "select",
        "rank": 2,
        "shown_ranks": [1, 2],
    }
    assert (await client.post(path, json=data)).status_code == 409
    data["selection_id"] = str(selection_id)
    assert (await client.post(path, json=data)).status_code == 200
    data["action"] = "dislike"
    assert (await client.post(path, json=data)).status_code == 200
    data["action"] = "skip"
    r = (await client.post(path, json=data)).json()
    assert r["skipped"] == 1 and r["confirmed"] == 0
    async with sessions() as db:
        s = await db.get(PhotoSelectionSession, selection_id)
        assert not s.confirmed_at and not s.chosen_rank
        assert (
            await db.get(PhotoSelectionCandidate, (selection_id, 2))
        ).operator_rating == "dislike"
        assert not (await db.get(PhotoSelectionCandidate, (selection_id, 3))).displayed
    data.update(action="confirm", rank=3, shown_ranks=[1, 2])
    assert (await client.post(path, json=data)).status_code == 409
    await http.aclose()


@pytest.mark.integration
async def test_dislike_persists_and_advances_alternative_without_confirmation(
    client, sessions, tmp_path
):
    _, sid, selection_id, t, b, _, http = await batch_fixture(client, sessions, tmp_path)
    try:
        async with sessions() as db:
            original_asset = (await db.get(Submission, sid)).media_asset_id
        path = f"/api/v1/review-batches/{b['id']}/items/0"
        response = await client.post(
            path + "/action",
            json={
                "selection_id": str(selection_id),
                "reviewer_id": t,
                "action": "dislike",
                "rank": 1,
                "shown_ranks": [1],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["next_rank"] == 2
        assert response.json()["current_position"] == 0
        assert response.json()["confirmed"] == 0
        restored = (await client.get(path)).json()
        assert restored["active_rank"] == 2
        assert restored["candidates"][0]["rating"] == "dislike"
        assert restored["candidates"][1]["rating"] is None
        async with sessions() as db:
            selection = await db.get(PhotoSelectionSession, selection_id)
            assert selection.confirmed_at is None and selection.chosen_rank is None
            assert (await db.get(Submission, sid)).media_asset_id == original_asset
            assert not (await db.get(PhotoSelectionCandidate, (selection_id, 2))).displayed
            assert not (await db.get(PhotoSelectionCandidate, (selection_id, 3))).displayed
    finally:
        await http.aclose()


@pytest.mark.integration
@pytest.mark.parametrize("unavailable", ["dislike", "no_image"])
async def test_dislike_wraps_skips_unavailable_and_reports_exhaustion(
    client, sessions, tmp_path, unavailable
):
    _, _, selection_id, t, b, _, http = await batch_fixture(client, sessions, tmp_path)
    try:
        async with sessions() as db, db.begin():
            third = await db.get(PhotoSelectionCandidate, (selection_id, 3))
            if unavailable == "dislike":
                third.operator_rating = "dislike"
            else:
                third.media_asset_id = None
        path = f"/api/v1/review-batches/{b['id']}/items/0"
        data = {
            "selection_id": str(selection_id),
            "reviewer_id": t,
            "action": "dislike",
            "rank": 2,
            "shown_ranks": [2],
        }
        result = await client.post(path + "/action", json=data)
        assert result.status_code == 200 and result.json()["next_rank"] == 1
        data.update(rank=1, shown_ranks=[1, 2])
        result = await client.post(path + "/action", json=data)
        assert result.status_code == 200 and result.json()["next_rank"] is None
        assert result.json()["current_position"] == 0
        restored = (await client.get(path)).json()
        assert restored["active_rank"] == 1
        assert restored["state"] == "pending"
        assert all(c["rating"] == "dislike" for c in restored["candidates"][:2])
        async with sessions() as db:
            assert not (await db.get(PhotoSelectionCandidate, (selection_id, 3))).displayed
            assert (await db.get(PhotoSelectionSession, selection_id)).confirmed_at is None
    finally:
        await http.aclose()


@pytest.mark.integration
async def test_dislike_rejected_when_closed_preserves_rating_and_selection(
    client, sessions, tmp_path
):
    from dropgrid.db.models import PhotoReviewBatch

    _, _, selection_id, t, b, _, http = await batch_fixture(client, sessions, tmp_path)
    try:
        async with sessions() as db, db.begin():
            batch = await db.get(PhotoReviewBatch, UUID(b["id"]))
            batch.state = "closed"
        result = await client.post(
            f"/api/v1/review-batches/{b['id']}/items/0/action",
            json={
                "selection_id": str(selection_id),
                "reviewer_id": t,
                "action": "dislike",
                "rank": 1,
                "shown_ranks": [1],
            },
        )
        assert result.status_code == 409
        async with sessions() as db:
            assert (await db.get(PhotoSelectionSession, selection_id)).proposed_rank == 1
            assert (
                await db.get(PhotoSelectionCandidate, (selection_id, 1))
            ).operator_rating is None
    finally:
        await http.aclose()


@pytest.mark.integration
async def test_category_cannot_reuse_same_source_target(sessions, tmp_path):
    _, _, _, ref, _, provider, planner, item, http = await archive_fixture(sessions, tmp_path)
    with pytest.raises(PhotoError, match="category_archive_context_changed"):
        await materialize_archive(provider, planner, item, "Cats", target_id=ref.community_id)
    await http.aclose()


@pytest.mark.integration
async def test_retry_scopes_only_failed_and_preserves_success(client, sessions):
    from dropgrid.db.models import Account, CampaignPreparationJob, MediaAsset

    cid = await prepared(sessions, count=10)
    async with sessions() as db, db.begin():
        db.add(Account(name="mock", vk_user_id=123, encrypted_access_token="mock"))
        asset = MediaAsset(storage_key="test.jpg")
        db.add(asset)
        await db.flush()
        rows = list((await db.scalars(select(Submission).order_by(Submission.id))).all())
        for row in rows[:8]:
            row.media_asset_id = asset.id
        failed = {str(row.id) for row in rows[8:]}
    result = await client.post(f"/api/v1/campaigns/{cid}/retry-failed-photos")
    assert result.status_code == 200, result.text
    assert result.json()["total"] == 2
    async with sessions() as db:
        job = await db.scalar(select(CampaignPreparationJob))
        assert set(job.submission_scope) == failed
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.media_asset_id.is_not(None))
            )
            == 8
        )


@pytest.mark.integration
async def test_batch_creator_close_and_open_guard(client, sessions, tmp_path):
    _, _, sid, t, b, _, http = await batch_fixture(client, sessions, tmp_path)
    owner = (await client.post("/api/v1/photo-reviewers", json={"display_name": "Tony"})).json()[
        "id"
    ]
    path = f"/api/v1/review-batches/{b['id']}"
    result = await client.patch(path, json={"created_by": owner, "state": "closed"})
    assert result.status_code == 200 and result.json()["created_by"] == owner
    assert (
        await client.post(
            path + "/items/0/action",
            json={
                "selection_id": str(sid),
                "reviewer_id": t,
                "rank": 1,
                "shown_ranks": [1],
                "action": "confirm",
            },
        )
    ).status_code == 409
    async with sessions() as db:
        assert (await db.get(PhotoSelectionSession, sid)).confirmed_at is None
    await http.aclose()


@pytest.mark.integration
async def test_retry_timing_uses_current_run_not_original_job_creation(sessions, tmp_path):
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from dropgrid.config import Settings
    from dropgrid.db.models import Account, CampaignPreparationJob, utcnow
    from dropgrid.photos.campaign_preparation import CampaignPreparation, enqueue
    from dropgrid.photos.readiness import report

    cid = await prepared(sessions, count=1)
    engine = SimpleNamespace(planner=SimpleNamespace(settings=Settings(_env_file=None)))
    async with sessions() as db, db.begin():
        db.add(Account(name="mock", vk_user_id=123, encrypted_access_token="mock"))
        await db.flush()
        job = await enqueue(db, cid, engine)
        job.created_at = utcnow() - timedelta(days=7)
    prep = SimpleNamespace(
        sessions=sessions,
        prepare_community_media_context=AsyncMock(
            return_value=SimpleNamespace(
                resolution_status="resolved", references_ready=True, warnings=[]
            )
        ),
    )
    worker = CampaignPreparation(engine, prep)
    assert await worker.tick() == 1
    async with sessions() as db:
        job = await db.scalar(select(CampaignPreparationJob))
        assert job.result["warmup_seconds"] < 60 and job.result["queue_wait_seconds"] >= 0
        assert (await report(db, cid, engine.planner.settings))["duration_seconds"] < 60
