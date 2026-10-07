import asyncio
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from photo_fixtures import candidate, image_bytes
from sqlalchemy import func, select

from dropgrid.config import Settings
from dropgrid.db.models import (
    Campaign,
    Community,
    Grid,
    GridCommunity,
    MediaAsset,
    MediaProviderImport,
    PhotoSearchCache,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import CampaignStatus
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import FakePhotoProvider, PhotoError, PhotoPolicy, PhotoSearch
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.schemas import MediaPlanInput
from dropgrid.services.catalog import ConflictError

pytestmark = pytest.mark.integration


async def test_sensitive_provider_is_not_searched(sessions, tmp_path):
    cid = await prepared(sessions, count=1, category="ЗНАКОМСТВА")
    provider = FakePhotoProvider((candidate(),))
    provider.supports_sensitive_context = False
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
    ) as client:
        report = await make_planner(sessions, tmp_path, provider, client).plan(
            cid, MediaPlanInput()
        )
    assert report.unassigned == 1 and not provider.calls
    assert "provider_context_restricted" in report.categories[0].warnings


async def test_force_preserves_nonpending_history(sessions, tmp_path):
    from dropgrid.domain.enums import SubmissionStatus

    cid = await prepared(sessions, count=4)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        )
    ) as client:
        planner = make_planner(sessions, tmp_path, FakePhotoProvider((candidate(),)), client)
        await planner.plan(cid, MediaPlanInput())
        async with sessions() as db, db.begin():
            submission = await db.scalar(
                select(Submission)
                .where(Submission.media_asset_id.is_not(None))
                .order_by(Submission.id)
                .limit(1)
            )
            sid, aid = submission.id, submission.media_asset_id
            submission.status, submission.attempt_count = SubmissionStatus.failed, 2
        await planner.plan(cid, MediaPlanInput(force=True, max_reuse_per_asset=1))
        async with sessions() as db:
            submission = await db.get(Submission, sid)
            assert (
                submission.media_asset_id == aid
                and submission.status == SubmissionStatus.failed
                and submission.attempt_count == 2
            )


@pytest.mark.parametrize("change", ["status", "category"])
async def test_final_revalidation_preserves_history(sessions, tmp_path, change):
    cid = await prepared(sessions, count=1)

    class Changing(FakePhotoProvider):
        async def search(self, search):
            async with sessions() as db, db.begin():
                if change == "status":
                    campaign = await db.get(Campaign, cid)
                    campaign.status = CampaignStatus.running
                else:
                    member = await db.scalar(select(GridCommunity))
                    member.category = "СОБАКИ"
            return await super().search(search)

    provider = Changing((candidate(),))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        )
    ) as client:
        with pytest.raises(ConflictError):
            await make_planner(sessions, tmp_path, provider, client).plan(cid, MediaPlanInput())
    async with sessions() as db:
        assert all(
            s.media_asset_id is None and s.attempt_count == 0
            for s in (await db.scalars(select(Submission))).all()
        )


async def test_timeout_returns_partial_and_releases_lease(sessions, tmp_path):
    from dropgrid.db.models import PhotoPlanLease

    cid = await prepared(sessions, count=1)

    class Slow(FakePhotoProvider):
        async def search(self, search):
            await asyncio.sleep(1)
            return await super().search(search)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
    ) as client:
        planner = make_planner(sessions, tmp_path, Slow((candidate(),)), client)
        planner.policy = replace(planner.policy, plan_timeout_seconds=0.1)
        report = await planner.plan(cid, MediaPlanInput())
        assert report.unassigned == 1 and "planning_timeout" in report.categories[0].warnings
    async with sessions() as db:
        assert await db.get(PhotoPlanLease, cid) is None


async def test_partial_reuse_ceiling(sessions, tmp_path):
    cid = await prepared(sessions, count=4)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        )
    ) as client:
        report = await make_planner(
            sessions, tmp_path, FakePhotoProvider((candidate(),)), client
        ).plan(cid, MediaPlanInput())
    assert report.newly_assigned == 3 and report.unassigned == 1 and report.unique_assets == 1


async def prepared(sessions, count=8, category="ГРУЗОВИКИ"):
    async with sessions() as db, db.begin():
        grid = Grid(name="Photo test")
        db.add(grid)
        await db.flush()
        campaign = Campaign(
            name="Photo campaign",
            grid_id=grid.id,
            track_url="https://vk.ru/audio1_2",
            status=CampaignStatus.ready,
        )
        db.add(campaign)
        await db.flush()
        for i in range(count):
            community = Community(domain=f"photo{i:03}", category="КОШКИ")
            db.add(community)
            await db.flush()
            db.add(GridCommunity(grid_id=grid.id, community_id=community.id, category=category))
            db.add(Submission(campaign_id=campaign.id, community_id=community.id))
        return campaign.id


async def public(host):
    return ["93.184.216.34"]


def make_planner(sessions, tmp_path, provider, client, **settings):
    config = Settings(_env_file=None, media_storage_dir=tmp_path, **settings)
    policy = replace(PhotoPolicy(), hamming_threshold=1)
    return CampaignMediaPlanner(
        sessions,
        SearchCache(sessions, provider),
        PhotoDownloader(client, policy, public),
        LocalMediaStorage(tmp_path),
        config,
        policy,
    )


async def test_plan_category_idempotent_balanced_and_no_usage_increment(sessions, tmp_path):
    cid = await prepared(sessions)
    provider = FakePhotoProvider(tuple(candidate(i) for i in (1, 2, 3)))
    downloads = []

    def handler(request):
        downloads.append(request.url)
        return httpx.Response(
            200,
            content=image_bytes(seed=int(request.url.path.rsplit("/", 1)[1].split(".")[0])),
            headers={"content-type": "image/jpeg"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = make_planner(sessions, tmp_path, provider, client)
        report = await planner.plan(cid, MediaPlanInput())
        assert report.total_submissions == report.newly_assigned == 8
        assert report.unassigned == 0 and report.unique_assets == 3
        assert report.downloaded_assets == len(downloads) == 3
        assert provider.calls[0].query == "грузовик дорога"
        async with sessions() as db:
            first = {s.id: s.media_asset_id for s in (await db.scalars(select(Submission))).all()}
            assert max(Counter(first.values()).values()) <= 3
            assert all(
                a.usage_count == 0 and a.last_used_at is None and a.category == "грузовики"
                for a in (await db.scalars(select(MediaAsset))).all()
            )
        requests = len(provider.calls)
        again = await planner.plan(cid, MediaPlanInput())
        assert again.previously_assigned == 8 and again.newly_assigned == 0
        assert len(provider.calls) == requests and len(downloads) == 3
        async with sessions() as db:
            assert {
                s.id: s.media_asset_id for s in (await db.scalars(select(Submission))).all()
            } == first
        forced = await planner.plan(cid, MediaPlanInput(force=True, max_reuse_per_asset=1))
        assert forced.unassigned == 5 and forced.unique_assets == 3
        assert len(downloads) == 3


async def prepared_different(sessions, count=2):
    async with sessions() as db, db.begin():
        old = await db.scalar(select(Campaign).limit(1))
        new = Campaign(
            name="Second campaign",
            grid_id=old.grid_id,
            track_url=old.track_url,
            status=CampaignStatus.ready,
        )
        db.add(new)
        await db.flush()
        members = (
            await db.scalars(
                select(GridCommunity.community_id)
                .where(GridCommunity.grid_id == old.grid_id)
                .limit(count)
            )
        ).all()
        db.add_all([Submission(campaign_id=new.id, community_id=member) for member in members])
        return new.id


async def test_partial_download_bounds_and_provider_id_reuse(sessions, tmp_path):
    cid = await prepared(sessions, count=2)
    provider = FakePhotoProvider(tuple(candidate(i) for i in range(1, 101)))
    downloads = []

    def handler(request):
        downloads.append(request.url)
        return httpx.Response(
            200,
            content=image_bytes(seed=int(request.url.path.rsplit("/", 1)[1].split(".")[0])),
            headers={"content-type": "image/jpeg"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = make_planner(sessions, tmp_path, provider, client)
        report = await planner.plan(cid, MediaPlanInput())
        assert report.unique_assets == 2 and len(downloads) == 2
        cid2 = await prepared_different(sessions, count=2)
        report2 = await planner.plan(cid2, MediaPlanInput())
        assert report2.newly_assigned == 2 and report2.reused_existing_assets == 2
        assert len(downloads) == 2 and len(provider.calls) == 1


async def test_missing_provider_partial_and_no_corruption(sessions, tmp_path):
    cid = await prepared(sessions, count=4)

    class Unavailable:
        name = "unavailable"

        async def search(self, search):
            raise PhotoError("provider_unavailable")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No download expected"))
    ) as client:
        report = await make_planner(sessions, tmp_path, Unavailable(), client).plan(
            cid, MediaPlanInput()
        )
    assert report.unassigned == 4 and report.newly_assigned == 0
    assert "provider_unavailable" in report.categories[0].warnings
    async with sessions() as db:
        campaign = await db.get(Campaign, cid)
        assert campaign.status == CampaignStatus.ready
        assert all(s.attempt_count == 0 for s in (await db.scalars(select(Submission))).all())


async def test_concurrent_plan_leases(sessions, tmp_path):
    cid = await prepared(sessions, count=1)
    started, release = asyncio.Event(), asyncio.Event()

    class Slow(FakePhotoProvider):
        async def search(self, search):
            started.set()
            await release.wait()
            return await super().search(search)

    provider = Slow((candidate(),))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        )
    ) as client:
        planner = make_planner(sessions, tmp_path, provider, client)
        task = asyncio.create_task(planner.plan(cid, MediaPlanInput()))
        await started.wait()
        with pytest.raises(ConflictError, match="already running"):
            await planner.plan(cid, MediaPlanInput())
        release.set()
        assert (await task).newly_assigned == 1
        assert (await planner.plan(cid, MediaPlanInput())).previously_assigned == 1
    assert len(provider.calls) == 1


async def test_cache_persistence_normalization_expiration_and_coalescing(sessions):
    provider = FakePhotoProvider((candidate(),))
    cache = SearchCache(sessions, provider)
    search = PhotoSearch(query=" TRUCK   highway ", lang="en")
    assert not (await cache.search(search))[1]
    second = SearchCache(sessions, FakePhotoProvider())
    assert (await second.search(PhotoSearch(query="truck highway", lang="en")))[1]
    assert search.cache_key("fake") == PhotoSearch(query="truck highway", lang="en").cache_key(
        "fake"
    )
    assert search.cache_key("fake") != search.model_copy(
        update={"orientation": "vertical"}
    ).cache_key("fake")
    async with sessions() as db, db.begin():
        row = await db.get(PhotoSearchCache, search.cache_key("fake"))
        assert row.expires_at >= utcnow() + timedelta(hours=23)
        row.expires_at = utcnow() - timedelta(seconds=1)
    results = await asyncio.gather(*(cache.search(search) for _ in range(3)))
    assert sorted(hit for _, hit in results) == [False, True, True]
    assert len(provider.calls) == 2


async def test_exact_near_provider_alias_dedup(sessions, tmp_path):
    cid = await prepared(sessions, count=3)
    provider = FakePhotoProvider(tuple(candidate(i) for i in (1, 2, 3)))

    def handler(request):
        size = (1200, 1200) if request.url.path.endswith("3.jpg") else (1000, 1000)
        return httpx.Response(
            200, content=image_bytes(seed=1, size=size), headers={"content-type": "image/jpeg"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = make_planner(sessions, tmp_path, provider, client)
        report = await planner.plan(cid, MediaPlanInput())
    assert report.unique_assets == 1 and report.newly_assigned == 3
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MediaAsset)) == 1
        assert await db.scalar(select(func.count()).select_from(MediaProviderImport)) == 3


async def test_media_api_library_content_filters_and_missing_key(client, sessions, tmp_path):
    cid = await prepared(sessions, count=1)
    response = await client.post(f"/api/v1/campaigns/{cid}/media/plan", json={"force": False})
    assert response.status_code == 200 and response.json()["unassigned"] == 1
    assert "provider_unavailable" in response.json()["categories"][0]["warnings"]
    listing = await client.get("/api/v1/media-assets?enabled=true&provider=pixabay&page_size=1")
    assert listing.json() == {"items": [], "total": 0, "page": 1, "page_size": 1}
    assert (await client.get(f"/api/v1/media-assets/{UUID(int=1)}/content")).status_code == 404
    assert (
        await client.post(
            f"/api/v1/campaigns/{cid}/media/plan", json={"download_url": "https://evil.test"}
        )
    ).status_code == 422


async def test_media_content_filters_and_unique_constraints(sessions, database_url, tmp_path):
    from sqlalchemy.exc import IntegrityError

    from dropgrid.api.app import create_app
    from dropgrid.photos.images import normalize_image

    storage = LocalMediaStorage(tmp_path)
    image = normalize_image(image_bytes(), PhotoPolicy())
    key = storage.write(image)
    async with sessions() as db, db.begin():
        asset = MediaAsset(
            storage_key=key,
            category="кошки",
            provider="fake",
            provider_asset_id="1",
            sha256=image.sha256,
            perceptual_hash=image.perceptual_hash,
            mime_type="image/jpeg",
            width=image.width,
            height=image.height,
            byte_size=len(image.data),
            enabled=True,
        )
        db.add(asset)
        await db.flush()
        aid = asset.id
    app = create_app(
        Settings(_env_file=None, database_url=database_url, media_storage_dir=tmp_path)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        listing = (
            await client.get(
                "/api/v1/media-assets?category=кошки&provider=fake&enabled=true&page_size=1"
            )
        ).json()
        assert listing["total"] == 1 and listing["items"][0]["id"] == str(aid)
        assert "storage_key" not in listing["items"][0]
        assert (await client.get("/api/v1/media-assets?category=собаки")).json()["total"] == 0
        assert (await client.get(f"/api/v1/media-assets/{aid}")).status_code == 200
        response = await client.get(f"/api/v1/media-assets/{aid}/content")
        assert response.status_code == 200 and response.content == image.data
        assert response.headers["content-type"] == "image/jpeg"
    for values in ({"provider": "fake", "provider_asset_id": "1"}, {"sha256": image.sha256}):
        with pytest.raises(IntegrityError):
            async with sessions() as db, db.begin():
                db.add(MediaAsset(storage_key="legacy", **values))
                await db.flush()


async def test_cache_credentials_independent_and_shared_rate_limit(sessions):
    from pydantic import SecretStr

    from dropgrid.db.models import PhotoProviderState
    from dropgrid.photos.pixabay import PixabayPhotoProvider

    search = PhotoSearch(query="flowers", lang="en")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={"hits": []},
                headers={
                    "X-RateLimit-Limit": "50",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "30",
                },
            )
        )
    ) as client:
        provider = PixabayPhotoProvider(SecretStr("key-one"), client)
        cache = SearchCache(sessions, provider)
        assert not (await cache.search(search))[1]
        changed = SearchCache(sessions, PixabayPhotoProvider(SecretStr("key-two"), client))
        assert (await changed.search(search))[1]
        with pytest.raises(PhotoError, match="provider_rate_limited"):
            await changed.search(PhotoSearch(query="cats"))
        async with sessions() as db:
            state = await db.get(PhotoProviderState, "pixabay")
            assert state.interval_seconds == 1.2 and state.blocked_until > utcnow()


async def test_cross_campaign_import_race_and_lifecycle_revalidation(sessions, tmp_path):
    cid = await prepared(sessions, count=1)
    cid2 = await prepared_different(sessions, count=1)
    provider = FakePhotoProvider((candidate(),))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        )
    ) as client:
        planner = make_planner(sessions, tmp_path, provider, client)
        reports = await asyncio.gather(
            planner.plan(cid, MediaPlanInput()), planner.plan(cid2, MediaPlanInput())
        )
        assert all(r.newly_assigned == 1 for r in reports)
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(MediaAsset)) == 1
            assert await db.scalar(select(func.count()).select_from(MediaProviderImport)) == 1
        async with sessions() as db, db.begin():
            campaign = await db.get(Campaign, cid)
            campaign.status = CampaignStatus.completed
        with pytest.raises(ConflictError):
            await planner.plan(cid, MediaPlanInput(force=True))
