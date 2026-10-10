from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from test_archive_integration import cached_reference
from test_photo_integration import make_planner, prepared
from test_product_workflow import account, rows, unisex

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Campaign,
    CampaignPreparationJob,
    Community,
    CommunityMediaUsage,
    MediaAsset,
    MediaProviderImport,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
    utcnow,
)
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import VKWriteDisabledError
from dropgrid.integrations.vk.photos import WallPhotoUploader
from dropgrid.integrations.vk.read_only import preparation_read_only
from dropgrid.photos.archive import VKArchivePhotoProvider
from dropgrid.photos.campaign_preparation import PHOTO_BATCH_SIZE, CampaignPreparation, enqueue
from dropgrid.photos.domain import FakePhotoProvider, PhotoError
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.pool import PoolCandidate, materialize_archive
from dropgrid.photos.review import choose, confirm
from dropgrid.photos.schemas import MediaPlanRead
from dropgrid.photos.visual import FakeVisualEmbedder
from dropgrid.services.account_pools import distribute
from dropgrid.services.catalog import ConflictError
from dropgrid.services.sending import start_campaign


@pytest.mark.parametrize(
    "n,assigned,missing", [(1, 100, 414), (3, 300, 214), (5, 500, 14), (6, 514, 0)]
)
def test_514_capacity(n, assigned, missing):
    targets = rows(514)
    result, errors = distribute(
        targets, [(account(i + 1), 100, i) for i in range(n)], unisex(targets)
    )
    assert len(result) == assigned and len(errors) == missing
    assert max(Counter(result.values()).values()) <= 100
    assert set(errors.values()) <= {"account_capacity_exhausted"}


@pytest.mark.integration
async def archive_fixture(sessions, tmp_path):
    cid = await prepared(sessions, count=1, category="Cats")
    async with sessions() as db, db.begin():
        target = await db.scalar(select(Submission))
        source = Community(domain="category-source", category="Cats")
        db.add(source)
        await db.flush()
        target_id, sid = target.community_id, target.id
    embedder = FakeVisualEmbedder()
    refs = LocalMediaStorage(tmp_path / "refs")
    ref, image = await cached_reference(sessions, refs, embedder, source.id, style=True, age=1)
    provider = VKArchivePhotoProvider(SimpleNamespace(sessions=sessions, storage=refs))
    provider.prepare = AsyncMock(return_value=ref)
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail("No HTTP")))
    planner = make_planner(sessions, tmp_path / "assets", FakePhotoProvider(), http)
    planner.settings = Settings(_env_file=None, cross_community_reuse_enabled=True)
    planner.archive = provider
    photo = provider.photo(ref, "Cats").model_copy(
        update={"provider": "vk_category_archive", "license_code": "cross-community-reuse"}
    )
    return (
        cid,
        sid,
        target_id,
        ref,
        image,
        provider,
        planner,
        PoolCandidate(photo, "vk_category_archive", reference=ref),
        http,
    )


@pytest.mark.integration
async def test_category_selected_only_import_identity_sha_phash_and_provenance(sessions, tmp_path):
    _, _, target, ref, image, provider, planner, item, http = await archive_fixture(
        sessions, tmp_path
    )
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MediaAsset)) == 0
    asset, created = await materialize_archive(provider, planner, item, "Cats", target_id=target)
    assert created and asset.provider == "vk_category_archive"
    reused, created = await materialize_archive(provider, planner, item, "Cats", target_id=target)
    assert not created and reused.id == asset.id
    # SHA and near-duplicate aliases keep the original file and canonical provenance.
    async with sessions() as db, db.begin():
        saved = await db.get(MediaAsset, asset.id)
        saved.provider, saved.provider_asset_id = "library", "other"
        saved.perceptual_hash = "0000000000000000"
    item.reference.vk_photo_id += 1
    same_sha, created = await materialize_archive(provider, planner, item, "Cats", target_id=target)
    assert not created and same_sha.id == asset.id
    async with sessions() as db, db.begin():
        saved = await db.get(MediaAsset, asset.id)
        saved.sha256, saved.perceptual_hash = "0" * 64, image.perceptual_hash
    item.reference.vk_photo_id += 1
    near, created = await materialize_archive(provider, planner, item, "Cats", target_id=target)
    assert not created and near.id == asset.id
    async with sessions() as db:
        provenance = await db.get(MediaProviderImport, ("vk_category_archive", "-123_1"))
        assert (
            provenance.source_community_id == ref.community_id
            and provenance.source_post_id == ref.vk_post_id
        )
        assert provenance.source_photo_owner_id == -123 and provenance.source_photo_id == 1
        assert (
            provenance.source_posted_at == ref.posted_at
            and provenance.source_sha256 == image.sha256
        )
        assert bytes.fromhex(provenance.source_embedding) == ref.embedding
        assert provenance.source_embedding_model == ref.embedding_model
        assert await db.scalar(select(func.count()).select_from(MediaAsset)) == 1
    assert len(list((tmp_path / "assets").rglob("*.jpg"))) == 1
    await http.aclose()


@pytest.mark.integration
async def test_category_target_cooldown_not_source_age(sessions, tmp_path):
    _, _, target, ref, _, provider, planner, item, http = await archive_fixture(sessions, tmp_path)
    async with sessions() as db, db.begin():
        db.add(
            CommunityMediaUsage(
                community_id=target,
                source_provider="library",
                source_identity="other",
                sha256="other",
                perceptual_hash=ref.perceptual_hash,
                first_used_at=utcnow(),
                last_used_at=utcnow(),
                use_count=1,
            )
        )
    with pytest.raises(PhotoError, match="community_media_cooldown"):
        await materialize_archive(provider, planner, item, "Cats", target_id=target)
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MediaAsset)) == 0
    await http.aclose()


@pytest.mark.integration
async def test_review_category_choice_confirm_and_displayed_only(sessions, tmp_path):
    cid, sid, target, ref, _, provider, planner, item, http = await archive_fixture(
        sessions, tmp_path
    )
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        campaign.photo_review_mode = "REVIEW_BEFORE_SEND"
        context = PhotoSelectionSession(
            campaign_id=cid,
            submission_id=sid,
            community_id=target,
            category="Cats",
            baseline_rank=1,
            proposed_rank=1,
        )
        db.add(context)
        await db.flush()
        for rank in (1, 2, 3):
            db.add(
                PhotoSelectionCandidate(
                    selection_session_id=context.id,
                    rank=rank,
                    provider="vk_category_archive",
                    source_identity=item.photo.provider_asset_id,
                    reference_id=ref.id,
                    features={},
                    candidate=item.photo.model_dump(mode="json"),
                )
            )
        await choose(db, sid, 2, shown_ranks=[1, 2])
    async with sessions() as db:
        assert (await db.get(PhotoSelectionSession, context.id)).confirmed_at is None
        assert await db.scalar(select(func.count()).select_from(MediaAsset)) == 0
    await confirm(planner, sid)
    async with sessions() as db:
        selection = await db.get(PhotoSelectionSession, context.id)
        assert selection.confirmed_at and selection.chosen_rank == 2
        candidates = (
            await db.scalars(select(PhotoSelectionCandidate).order_by(PhotoSelectionCandidate.rank))
        ).all()
        assert [c.displayed for c in candidates] == [True, True, False]
        assert [c.selected for c in candidates] == [False, True, False]
        assert (await db.get(Submission, sid)).photo_source == "vk_category_archive"
    await http.aclose()


@pytest.mark.integration
async def test_514_durable_bounded_batches_restart_and_readonly_guard(sessions, tmp_path):
    cid = await prepared(sessions, count=514)
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        campaign.is_dry_run = True
        a = Account(name="Test metadata", vk_user_id=1, encrypted_access_token="mock")
        db.add(a)
        await db.flush()
        aid = a.id
    calls = []

    async def prepare_context(*args):
        assert preparation_read_only.get()
        return SimpleNamespace(resolution_status="resolved", references_ready=True, warnings=[])

    async def plan(campaign_id, data, *, submission_ids):
        assert preparation_read_only.get() and len(submission_ids) <= PHOTO_BATCH_SIZE
        calls.extend(submission_ids)
        return MediaPlanRead(campaign_id=campaign_id, total_submissions=514)

    planner = SimpleNamespace(settings=Settings(_env_file=None), plan=plan)
    engine = SimpleNamespace(planner=planner, storage=LocalMediaStorage(tmp_path))
    preparation = SimpleNamespace(
        sessions=sessions, prepare_community_media_context=prepare_context
    )
    async with sessions() as db, db.begin():
        await enqueue(db, cid, engine, [aid])
    # Restart a worker instance at every tick; progress is exclusively durable.
    for _ in range(257 + (514 + PHOTO_BATCH_SIZE - 1) // PHOTO_BATCH_SIZE):
        assert await CampaignPreparation(engine, preparation).tick() == 1
    assert len(calls) == len(set(calls)) == 514
    async with sessions() as db, db.begin():
        job = await db.scalar(select(CampaignPreparationJob))
        assert job.state == "ready" and job.completed == 514
        with pytest.raises(ConflictError, match="Dry-run campaigns cannot be sent"):
            await start_campaign(db, cid, aid, None, engine.storage, planner.settings)
    assert not preparation_read_only.get()


async def test_readonly_guard_zero_vk_writes_and_multipart_even_when_enabled():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"response": []})

    async with (
        VKClient(
            Settings(_env_file=None, vk_write_enabled=True), transport=httpx.MockTransport(handle)
        ) as vk,
        WallPhotoUploader(vk, transport=httpx.MockTransport(handle)) as uploader,
    ):
        marker = preparation_read_only.set(True)
        try:
            for method in ("photos.getWallUploadServer", "photos.saveWallPhoto", "wall.post"):
                with pytest.raises(VKWriteDisabledError):
                    await vk.call(method, access_token="mock", account_id=UUID(int=1))
            with pytest.raises(VKWriteDisabledError):
                await uploader.upload(
                    b"unused", community_id=123, account_id=UUID(int=1), access_token="mock"
                )
        finally:
            preparation_read_only.reset(marker)
    assert calls == []


@pytest.mark.integration
async def test_dashboard_before_threshold(client):
    status = (await client.get("/api/v1/photo-ranking")).json()
    assert status["choices"] == 0 and status["minimum"] == 100 and status["remaining"] == 100
    assert status["retrain_interval"] == 25 and status["latest_model"] == "deterministic"


@pytest.mark.integration
async def test_dry_run_endpoint_scope_is_dedicated_and_immutable(client, sessions):
    cid = await prepared(sessions, count=3)
    async with sessions() as db, db.begin():
        original = await db.get(Campaign, cid)
        grid_id = original.grid_id
        a = Account(name="Mock", vk_user_id=123, encrypted_access_token="mock")
        db.add(a)
        await db.flush()
        usable = (await db.scalars(select(Community).order_by(Community.domain))).all()
        for i, community in enumerate(usable):
            community.resolution_status = "resolved"
            community.vk_group_id = 1000 + i
        usable[0].is_active = False
        selected = str(usable[1].id)
        aid = str(a.id)
    result = await client.post(
        "/api/v1/campaign-dry-runs",
        json={
            "grid_id": str(grid_id),
            "track_url": "https://vk.com/audio1_2",
            "account_ids": [aid],
            "community_ids": [selected],
            "photo_review_mode": "REVIEW_BEFORE_SEND",
        },
    )
    assert result.status_code == 200, result.text
    dry_id = result.json()["campaign_id"]
    assert result.json()["is_dry_run"] and result.json()["total"] == 1
    assert str(cid) != dry_id
    assert (
        await client.post(f"/api/v1/campaigns/{dry_id}/start", json={"account_ids": [aid]})
    ).status_code == 409
    assert (
        await client.patch(f"/api/v1/campaigns/{dry_id}", json={"is_dry_run": False})
    ).status_code == 422
    state = (await client.get(f"/api/v1/campaigns/{dry_id}")).json()
    assert state["is_dry_run"] and state["status"] == "ready"
    assert not (await client.get(f"/api/v1/campaigns/{cid}")).json()["is_dry_run"]


@pytest.mark.integration
async def test_real_campaign_planner_can_select_category_winner_only(sessions, tmp_path):
    from dropgrid.db.models import CommunityReferencePhoto
    from dropgrid.photos.schemas import MediaPlanInput
    from dropgrid.photos.visual import VisualEmbedding, serialize_embedding
    from dropgrid.photos.visual_library import VisualLibrary

    cid, sid, target, ref, _, provider, planner, _, http = await archive_fixture(sessions, tmp_path)
    embedder = FakeVisualEmbedder()
    target_ref, _ = await cached_reference(
        sessions,
        provider.collector.storage,
        embedder,
        target,
        photo_id=9,
        style=True,
        age=1,
        seed=9,
    )
    vector = VisualEmbedding(embedder.model, 3, (1, 0, 0))
    async with sessions() as db, db.begin():
        for rid in (ref.id, target_ref.id):
            row = await db.get(CommunityReferencePhoto, rid)
            row.embedding = serialize_embedding(vector)
    async with sessions() as db:
        ref = await db.get(CommunityReferencePhoto, ref.id)
    provider.prepare = AsyncMock(return_value=ref)
    provider.collector.client = SimpleNamespace(settings=planner.settings)
    planner.visual = VisualLibrary(sessions, planner.storage, embedder)
    result = await planner.plan(cid, MediaPlanInput())
    assert result.newly_assigned == 1 and result.unassigned == 0
    async with sessions() as db:
        row = await db.get(Submission, sid)
        assert row.photo_source == "vk_category_archive"
        asset = await db.get(MediaAsset, row.media_asset_id)
        assert asset.provider == "vk_category_archive"
        assert await db.scalar(select(func.count()).select_from(MediaAsset)) == 1
        assert await db.scalar(select(func.count()).select_from(PhotoSelectionSession)) == 1
        assert (await db.scalar(select(PhotoSelectionSession))).confirmed_at is None
    await http.aclose()


@pytest.mark.integration
async def test_sender_claim_excludes_dry_run_even_if_lifecycle_corrupt(sessions, tmp_path):
    from dropgrid.domain.enums import CampaignStatus
    from dropgrid.services.sending import CampaignSender

    cid = await prepared(sessions, count=1)
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, cid)
        campaign.is_dry_run = True
        campaign.status = CampaignStatus.running
        row = await db.scalar(select(Submission))
        row.vk_send_phase = "queued"
    sender = CampaignSender(
        sessions,
        SimpleNamespace(settings=Settings(_env_file=None, vk_write_enabled=True)),
        AsyncMock(),
        LocalMediaStorage(tmp_path),
        AsyncMock(),
    )
    assert await sender._claim(None) is None
    sender.uploader.upload.assert_not_called()
    sender.tokens.get_token.assert_not_called()


def test_514_gender_constrained_pool():
    from dropgrid.domain.enums import GenderTag

    male = rows(300, GenderTag.male)
    female = rows(214, GenderTag.female, offset=10_000)
    pool = [
        (account(i + 1, GenderTag.male if i < 3 else GenderTag.female), 100, i) for i in range(6)
    ]
    assigned, errors = distribute(male + female, pool, {})
    assert len(assigned) == 514 and not errors
    by_id = {a.id: a for a, _, _ in pool}
    assert all(
        by_id[assigned[row.id]].gender_tag == community.required_gender_tag
        for row, community in male + female
    )
    assert max(Counter(assigned.values()).values()) == 100


@pytest.mark.integration
@pytest.mark.parametrize("batch", [False, True])
async def test_approval_keeps_auto_choice_and_records_only_visible_comparisons(
    client, sessions, tmp_path, batch
):
    cid, sid, target, ref, _, _, planner, item, http = await archive_fixture(sessions, tmp_path)
    client._transport.app.state.photo_engine.planner = planner
    async with sessions() as db, db.begin():
        row = await db.get(Submission, sid)
        asset, _ = await materialize_archive(
            planner.archive, planner, item, "Cats", target_id=target
        )
        row.media_asset_id = asset.id
        decision = PhotoSelectionSession(
            campaign_id=cid,
            submission_id=sid,
            community_id=target,
            category="Cats",
            proposed_rank=1,
            baseline_rank=1,
        )
        db.add(decision)
        await db.flush()
        for rank in (1, 2, 3):
            db.add(
                PhotoSelectionCandidate(
                    selection_session_id=decision.id,
                    rank=rank,
                    provider="vk_category_archive",
                    source_identity=str(rank),
                    media_asset_id=asset.id,
                    features={},
                    candidate=item.photo.model_dump(mode="json"),
                )
            )
    body = {"selection_id": str(decision.id), "proposed_rank": 1, "shown_ranks": [1, 2]}
    if batch:
        path = f"/api/v1/campaigns/{cid}/photo-approve-all"
        body = {"reviews": [{"submission_id": str(sid), **body}]}
    else:
        path = f"/api/v1/submissions/{sid}/photo-approve"
    stale = {"selection_id": str(UUID(int=999)), "proposed_rank": 1, "shown_ranks": [1, 2]}
    stale_body = {"reviews": [{"submission_id": str(sid), **stale}]} if batch else stale
    assert (await client.post(path, json=stale_body)).status_code == 409
    async with sessions() as db:
        assert not (await db.get(PhotoSelectionSession, decision.id)).confirmed_at
        assert not any(
            c.displayed for c in (await db.scalars(select(PhotoSelectionCandidate))).all()
        )
    response = await client.post(path, json=body)
    assert response.status_code == 200, response.text
    async with sessions() as db:
        decision = await db.get(PhotoSelectionSession, decision.id)
        assert decision.chosen_rank == 1 and decision.confirmed_at
        choices = (
            await db.scalars(select(PhotoSelectionCandidate).order_by(PhotoSelectionCandidate.rank))
        ).all()
        assert [c.displayed for c in choices] == [True, True, False]
        assert [c.selected for c in choices] == [True, False, False]
    await http.aclose()


@pytest.mark.integration
@pytest.mark.parametrize("same_campaign", [False, True])
async def test_review_reuse_limit_is_per_campaign_not_dry_run_reservations(
    sessions, tmp_path, same_campaign
):
    cid, sid, target, ref, _, provider, planner, item, http = await archive_fixture(
        sessions, tmp_path
    )
    asset, _ = await materialize_archive(provider, planner, item, "Cats", target_id=target)
    async with sessions() as db, db.begin():
        current = await db.get(Campaign, cid)
        reserve = Campaign(
            name="Other dry run",
            grid_id=current.grid_id,
            track_url="https://vk.com/audio1_2",
            is_dry_run=True,
        )
        db.add(reserve)
        await db.flush()
        for i in range(3):
            c = Community(domain=f"reserved{i}")
            db.add(c)
            await db.flush()
            db.add(
                Submission(
                    campaign_id=cid if same_campaign else reserve.id,
                    community_id=c.id,
                    media_asset_id=asset.id,
                )
            )
        context = PhotoSelectionSession(
            campaign_id=cid,
            submission_id=sid,
            community_id=target,
            category="Cats",
            proposed_rank=1,
            baseline_rank=1,
        )
        db.add(context)
        await db.flush()
        db.add(
            PhotoSelectionCandidate(
                selection_session_id=context.id,
                rank=1,
                provider="vk_category_archive",
                source_identity=item.photo.provider_asset_id,
                media_asset_id=asset.id,
                features={},
                candidate=item.photo.model_dump(mode="json"),
            )
        )
    if same_campaign:
        with pytest.raises(ConflictError, match="Photo reuse limit reached"):
            await confirm(planner, sid)
    else:
        await confirm(planner, sid)
        async with sessions() as db:
            assert (await db.get(Submission, sid)).media_asset_id == asset.id
    await http.aclose()
