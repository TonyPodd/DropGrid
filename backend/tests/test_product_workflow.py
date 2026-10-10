from collections import Counter
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from photo_fixtures import candidate, image_bytes
from sqlalchemy import select
from test_account_tokens import TOKEN, configure
from test_photo_integration import make_planner, prepared
from test_vk_client import NoWaitLimiter

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Campaign,
    Community,
    PhotoRankingModel,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import AccountStatus, GenderTag
from dropgrid.integrations.vk.client import VKClient
from dropgrid.photos.domain import FakePhotoProvider
from dropgrid.photos.learning import (
    DIMENSIONS,
    features,
    predict,
    queue_training,
    temporal_split,
    train_one,
)
from dropgrid.photos.review import choose, confirm
from dropgrid.services.account_pools import distribute
from dropgrid.services.catalog import ConflictError


def account(i, gender=GenderTag.unspecified, status=AccountStatus.active):
    return Account(
        id=UUID(int=i),
        name=f"Account {i}",
        vk_user_id=i,
        encrypted_access_token="fake",
        status=status,
        gender_tag=gender,
    )


def rows(n, gender=None):
    return [
        (
            Submission(id=UUID(int=i + 1000)),
            Community(domain=f"community{i:04}", required_gender_tag=gender),
        )
        for i in range(n)
    ]


def test_balanced_capacity_and_determinism():
    accounts = [(account(i), 100, i) for i in (1, 2, 3)]
    submissions = rows(250)
    allocation, errors = distribute(submissions, accounts)
    assert not errors and len(allocation) == 250
    assert sorted(Counter(allocation.values()).values()) == [83, 83, 84]
    assert distribute(list(reversed(submissions)), list(reversed(accounts)))[0] == allocation
    allocation, errors = distribute(rows(314), accounts)
    assert len(allocation) == 300 and len(errors) == 14
    assert set(errors.values()) == {"account_capacity_exhausted"}


def test_genders_disabled_and_token_required():
    a = account(1, GenderTag.male)
    b = account(2, GenderTag.female)
    c = account(3, GenderTag.female, AccountStatus.disabled)
    d = account(4, GenderTag.female)
    d.encrypted_access_token = None
    allocation, errors = distribute(
        rows(3, GenderTag.female), [(a, 100, 0), (b, 2, 1), (c, 100, 2), (d, 100, 3)]
    )
    assert list(allocation.values()) == [b.id, b.id]
    assert list(errors.values()) == ["account_capacity_exhausted"]
    assert not distribute(rows(1, GenderTag.female), [(a, 100, 0)])[0]


@pytest.mark.integration
async def test_connect_reuses_identity_multiple_encrypted_tokens_and_disabled(client, sessions):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        token = dict(httpx.QueryParams(request.content.decode())).get("access_token")
        uid = 124 if token == TOKEN + "2" else 123
        return httpx.Response(
            200, json={"response": [{"id": uid, "first_name": "User", "last_name": str(uid)}]}
        )

    async with VKClient(
        Settings(_env_file=None), transport=httpx.MockTransport(handle), limiter=NoWaitLimiter()
    ) as vk:
        configure(client, sessions, vk)
        first = await client.post("/api/v1/accounts/connect", json={"access_token": TOKEN})
        assert first.status_code == 200, first.text
        aid = first.json()["id"]
        again = await client.post(
            "/api/v1/accounts/connect", json={"access_token": TOKEN + "replacement"}
        )
        assert again.json()["id"] == aid
        second = await client.post("/api/v1/accounts/connect", json={"access_token": TOKEN + "2"})
        assert second.json()["id"] != aid
        assert TOKEN not in first.text + again.text + second.text
        assert all(path.endswith("/users.get") for path in calls)
        async with sessions() as db:
            accounts = (await db.scalars(select(Account))).all()
            assert len(accounts) == 2 and all(
                a.last_validated_at and TOKEN not in a.encrypted_access_token for a in accounts
            )
            cipher = client._transport.app.state.token_cipher
            assert cipher.decrypt(
                accounts[0].encrypted_access_token, accounts[0].id
            ).get_secret_value() in (TOKEN + "replacement", TOKEN + "2")
        await client.patch(f"/api/v1/accounts/{aid}", json={"status": "disabled"})
        denied = await client.post("/api/v1/accounts/connect", json={"access_token": TOKEN})
        assert denied.status_code == 409


@pytest.mark.integration
async def test_connect_invalid_creates_nothing(client, sessions):
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": {"error_code": 5, "error_msg": "secret"}})
        ),
        limiter=NoWaitLimiter(),
    ) as vk:
        configure(client, sessions, vk)
        response = await client.post("/api/v1/accounts/connect", json={"access_token": TOKEN})
        assert (
            response.status_code == 401
            and TOKEN not in response.text
            and "secret" not in response.text
        )
        assert (await client.get("/api/v1/accounts")).json() == []


def decision(i):
    return PhotoSelectionSession(
        id=UUID(int=i + 1),
        category="Music",
        content_hint="девушка с машиной",
        baseline_rank=1,
        chosen_rank=2,
        confirmed_at=utcnow() + timedelta(seconds=i),
    )


def test_temporal_features_corrupt_model_baseline():
    ds = [decision(i) for i in range(100)]
    train, validation = temporal_split(list(reversed(ds)))
    assert len(train) == 80 and len(validation) == 20
    assert max(d.confirmed_at for d in train) < min(d.confirmed_at for d in validation)
    c = PhotoSelectionCandidate(
        rank=1,
        provider="pinterest",
        features={"normalized_visual_score": 0.8, "metadata_score": 1, "reference_count": 12},
    )
    assert len(features(c, ds[0])) == DIMENSIONS
    assert predict([c], ds[0], None) is None
    assert (
        predict(
            [c],
            ds[0],
            PhotoRankingModel(
                state="ready", feature_schema_version=1, coefficients=[float("nan")] * DIMENSIONS
            ),
        )
        is None
    )


@pytest.mark.integration
async def test_review_only_confirmed_displayed_train_and_cancel_guard(sessions, tmp_path):
    cid = await prepared(sessions, count=1, category="Music")
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        )
    )
    planner = make_planner(sessions, tmp_path, FakePhotoProvider([candidate()]), http)
    asset = (await planner._import(candidate(), "Music", False))[0]
    assert asset
    async with sessions() as db, db.begin():
        row = await db.scalar(select(Submission))
        campaign = await db.get(Campaign, cid)
        campaign.photo_review_mode = "REVIEW_BEFORE_SEND"
        row.media_asset_id = asset.id
        context = PhotoSelectionSession(
            campaign_id=cid,
            submission_id=row.id,
            community_id=row.community_id,
            category="Music",
            content_hint="woman car",
            baseline_rank=1,
            proposed_rank=1,
        )
        db.add(context)
        await db.flush()
        sid = row.id
        context_id = context.id
        for rank in (1, 2, 3):
            db.add(
                PhotoSelectionCandidate(
                    selection_session_id=context.id,
                    rank=rank,
                    provider="pixabay",
                    source_identity=str(rank),
                    media_asset_id=asset.id,
                    features={"metadata_score": rank / 3},
                    candidate=candidate().model_dump(mode="json"),
                )
            )
    async with sessions() as db, db.begin():
        context = await choose(db, sid, 2, "like", [1, 2])
        assert context.chosen_rank is None and context.confirmed_at is None
        assert await queue_training(db, Settings(_env_file=None)) is None
    await confirm(planner, sid)
    async with sessions() as db:
        context = await db.get(PhotoSelectionSession, context_id)
        assert context.chosen_rank == 2 and context.confirmed_at
        cs = (
            await db.scalars(select(PhotoSelectionCandidate).order_by(PhotoSelectionCandidate.rank))
        ).all()
        assert [c.displayed for c in cs] == [True, True, False]
        assert cs[1].operator_rating == "like" and cs[1].selected
        assert (await db.get(Campaign, cid)).preparation_state == "ready"
    async with sessions() as db, db.begin():
        with pytest.raises(ConflictError):
            await choose(db, sid, 1)
    await planner.downloader.client.aclose()


@pytest.mark.integration
@pytest.mark.parametrize("count,ready,unassigned", [(250, True, 0), (314, False, 14)])
async def test_pool_preflight_and_persisted_assignments(
    sessions, tmp_path, count, ready, unassigned
):
    from test_campaign_sender import seed

    from dropgrid.services.sending import cancel_campaign, preflight, start_campaign

    data = await seed(sessions, tmp_path, count=count)
    cid, aid, *_ = data
    storage = data[4]
    async with sessions() as db, db.begin():
        for i in (2, 3):
            db.add(
                Account(
                    name=f"Pool {i}",
                    vk_user_id=i,
                    encrypted_access_token="mock",
                    status=AccountStatus.active,
                    gender_tag=GenderTag.male,
                )
            )
    async with sessions() as db:
        report, _ = await preflight(db, cid, None, None, storage, Settings(_env_file=None))
        assert report["total_send_capacity"] == 300 and report["capacity_unassigned"] == unassigned
        assert report["ready"] == ready
        assert all(a["assigned"] <= 100 for a in report["assigned_per_account"])
    if ready:
        async with sessions() as db, db.begin():
            await start_campaign(db, cid, None, None, storage, Settings(_env_file=None))
        async with sessions() as db:
            subs = (await db.scalars(select(Submission))).all()
            assert len(set(s.account_id for s in subs)) == 3
            assert max(Counter(s.account_id for s in subs).values()) <= 100
            identities = {s.id: s.account_id for s in subs}
        async with sessions() as db, db.begin():
            await cancel_campaign(db, cid)
        async with sessions() as db:
            assert {
                s.id: s.account_id for s in (await db.scalars(select(Submission))).all()
            } == identities
            with pytest.raises(ConflictError):
                await start_campaign(db, cid, None, None, storage, Settings(_env_file=None))


@pytest.mark.integration
async def test_training_threshold_shadow_explicit_enable_and_retrain(sessions, tmp_path, client):
    from dropgrid.photos.domain import PhotoQueryBuilder
    from dropgrid.photos.pool import PoolCandidate
    from dropgrid.photos.review import rerank
    from dropgrid.photos.visual import RankedPhoto

    cid = await prepared(sessions, count=1)
    async with sessions() as db:
        sub = await db.scalar(select(Submission))
        sid, community_id = sub.id, sub.community_id

    async def add_choices(start, count):
        async with sessions() as db, db.begin():
            for i in range(start, start + count):
                context = PhotoSelectionSession(
                    campaign_id=cid,
                    submission_id=sid,
                    community_id=community_id,
                    category="Music",
                    content_hint="woman car",
                    baseline_rank=1,
                    proposed_rank=2,
                    chosen_rank=2,
                    confirmed_at=utcnow() + timedelta(seconds=i),
                )
                db.add(context)
                await db.flush()
                for rank in (1, 2, 3):
                    db.add(
                        PhotoSelectionCandidate(
                            selection_session_id=context.id,
                            rank=rank,
                            provider="pinterest",
                            source_identity=str(rank),
                            features={
                                "metadata_score": 0.1 if rank == 1 else 0.9 if rank == 2 else 1
                            },
                            candidate={},
                            displayed=rank < 3,
                            selected=rank == 2,
                        )
                    )

    await add_choices(0, 99)
    settings = Settings(_env_file=None)
    async with sessions() as db, db.begin():
        assert await queue_training(db, settings) is None
    await add_choices(99, 1)
    async with sessions() as db, db.begin():
        assert await queue_training(db, settings)
    assert await train_one(sessions, settings)
    async with sessions() as db:
        model = await db.scalar(select(PhotoRankingModel))
        assert model.metrics["train"] == 80 and model.metrics["validation"] == 20
        assert model.metrics["pairwise_examples"] == 80  # hidden rank 3 is never a negative
        assert model.metrics["baseline_top1"] == 0 and model.metrics["learned_top1"] == 1
        assert model.metrics["promotion_ready"]
        version = model.id
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail("No HTTP")))
    planner = make_planner(sessions, tmp_path, FakePhotoProvider(), http)
    # Only current bounded score features enter the model, never vectors or outcomes.
    items = [
        PoolCandidate(
            candidate(i),
            "pinterest",
            score=RankedPhoto(
                base_score=0,
                visual_score=None,
                final_score=0.9 - i * 0.1,
                best_similarity=None,
                reference_count=0,
                metadata_score=0.1 if i == 1 else 0.9,
            ),
        )
        for i in (1, 2)
    ]
    plan = PhotoQueryBuilder().build("Music", "woman car")
    assert (await rerank(planner, items, plan))[0] == items  # SHADOW
    response = await client.put("/api/v1/photo-ranking", json={"mode": "learned"})
    assert response.status_code == 200
    ranked, model_version = await rerank(planner, items, plan)
    assert ranked[0] is items[1] and model_version == f"pairwise-v1:{version}"
    await add_choices(100, 24)
    async with sessions() as db, db.begin():
        assert await queue_training(db, settings) is None
    await add_choices(124, 1)
    async with sessions() as db, db.begin():
        assert await queue_training(db, settings)
    async with sessions() as db, db.begin():
        model = await db.get(PhotoRankingModel, version)
        model.feature_schema_version = 999
    assert (await rerank(planner, items, plan))[1] == "deterministic-v1"
    assert (await client.put("/api/v1/photo-ranking", json={"mode": "learned"})).status_code == 409
    await http.aclose()


@pytest.mark.integration
async def test_durable_workflow_enqueue_idempotent_and_cancel_no_reads(client, sessions, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from test_reference_integration import seed

    from dropgrid.db.models import CampaignPreparationJob
    from dropgrid.photos.campaign_preparation import CampaignPreparation
    from dropgrid.services.sending import cancel_campaign

    _, aid = await seed(sessions)
    async with sessions() as db, db.begin():
        account = await db.get(Account, aid)
        account.encrypted_access_token = "mock"
    imported = (
        await client.post(
            "/api/v1/grids/import", json={"name": "Small", "text": "# Cats\nvk.com/club123"}
        )
    ).json()
    c = (
        await client.post(
            "/api/v1/campaigns",
            json={
                "name": "AUTO test",
                "grid_id": imported["grid"]["id"],
                "track_url": "https://vk.com/audio1_2",
            },
        )
    ).json()
    cid = UUID(c["id"])
    body = {"account_ids": [str(aid)]}
    first = await client.post(f"/api/v1/campaigns/{cid}/prepare-workflow", json=body)
    again = await client.post(f"/api/v1/campaigns/{cid}/prepare-workflow", json=body)
    assert first.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert first.json()["total"] == 1
    async with sessions() as db, db.begin():
        await cancel_campaign(db, cid)
    preparation = SimpleNamespace(
        sessions=sessions,
        prepare_community_media_context=AsyncMock(
            side_effect=AssertionError("No reads after cancellation")
        ),
    )
    engine = client._transport.app.state.photo_engine
    worker = CampaignPreparation(engine, preparation)
    assert await worker.tick() == 1
    async with sessions() as db:
        job = await db.scalar(select(CampaignPreparationJob))
        assert job.state == "cancelled"
        assert not (await db.scalar(select(Submission))).media_asset_id
    preparation.prepare_community_media_context.assert_not_called()


@pytest.mark.integration
async def test_connect_preserves_development_only_import_guard(client, sessions):
    async with VKClient(
        Settings(_env_file=None, app_env="production"),
        transport=httpx.MockTransport(lambda r: pytest.fail("No VK request in production import")),
        limiter=NoWaitLimiter(),
    ) as vk:
        configure(client, sessions, vk)
        assert (
            await client.post("/api/v1/accounts/connect", json={"access_token": TOKEN})
        ).status_code == 403
