from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from photo_fixtures import candidate, image_bytes
from sqlalchemy import func, select
from test_reference_integration import Tokens, post, resolver, seed

from dropgrid.config import Settings
from dropgrid.db.models import (
    Community,
    CommunityContentProfile,
    CommunityMediaUsage,
    CommunityReferencePhoto,
    MediaAsset,
    MediaProviderImport,
)
from dropgrid.integrations.vk.client import VKClient
from dropgrid.photos.archive import ArchiveDiscovery, VKArchivePhotoProvider
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import FakePhotoProvider, PhotoPolicy
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.pool import archive_pool, materialize_archive, rank_pool
from dropgrid.photos.preview import photo_preview
from dropgrid.photos.reference_schemas import PhotoPreviewInput
from dropgrid.photos.references import CommunityReferenceCollector, VKReferencePolicy
from dropgrid.photos.visual import FakeVisualEmbedder, VisualEmbedding, serialize_embedding
from dropgrid.photos.visual_library import VisualLibrary

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 9, tzinfo=UTC)


@pytest.mark.parametrize("scenario", ["window", "bound", "pinned", "active_bound"])
async def test_archive_pagination_independent_recent_target(sessions, tmp_path, scenario):
    cid, aid = await seed(sessions, target=1)
    rows = []
    if scenario == "active_bound":
        ages = [1] * 5000 + [200] * 2000 + [541] * 100
    elif scenario == "bound":
        ages = [1] * 300
    elif scenario == "pinned":
        ages = [700, 1, 200, 541]
    else:
        ages = [1] * 105 + [179, 180, 300, 540, 541, 600]
    for i, age in enumerate(ages, 1):
        p = post(i)
        p["date"] = int((NOW - timedelta(days=age)).timestamp())
        if scenario == "pinned" and i == 1:
            p["is_pinned"] = 1
        rows.append(p)
    offsets = []

    def handle(request):
        assert request.url.path == "/method/wall.get"
        params = parse_qs(request.content.decode())
        assert params["filter"] == ["owner"] and params["owner_id"] == ["-123"]
        offset = int(params["offset"][0])
        offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "response": {
                    "count": len(rows),
                    "items": rows[offset : offset + int(params["count"][0])],
                }
            },
        )

    async with (
        VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handle)) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: pytest.fail("Metadata discovery must not download")
            )
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            LocalMediaStorage(tmp_path),
            FakeVisualEmbedder(),
        )
        service = ArchiveDiscovery(collector)
        result = await service.sync(cid, aid, max_pages=2, now=NOW)
        if scenario == "active_bound":
            assert result.scan_bound_reached and result.pages_read == 2
            assert result.posts_scanned == 200 and result.start_offset > 4800
            assert result.seek_calls <= 48
            assert result.warnings == ["archive_scan_bound_reached"]
        elif scenario == "bound":
            assert result.exhausted
            assert result.seek_calls > 0
            assert result.start_offset >= 100
            assert result.candidates_discovered == 0
        else:
            assert result.crossed_max_age
            assert result.candidates_discovered == (1 if scenario == "pinned" else 3)
            assert result.posts_scanned <= len(rows)
            assert result.seek_calls > 0
            repeat = await service.sync(cid, aid, max_pages=2, now=NOW)
            assert (
                repeat.candidates_discovered == 0
                and repeat.candidates_existing == result.candidates_discovered
            )
        async with sessions() as s:
            refs = (await s.scalars(select(CommunityReferencePhoto))).all()
            assert all(
                not r.is_style_reference
                and r.archive_discovered
                and r.storage_key is None
                and r.embedding is None
                for r in refs
            )
            assert await s.scalar(select(func.count()).select_from(MediaAsset)) == 0
        assert await VKArchivePhotoProvider(collector).candidates(cid, now=NOW) == []


async def cached_reference(
    sessions, storage, embedder, cid, photo_id=1, style=False, age=200, seed=1
):
    image = normalize_image(image_bytes(seed=seed), PhotoPolicy())
    key = storage.write(image)
    vector = await embedder.embed_image(image.data)
    async with sessions() as s, s.begin():
        ref = CommunityReferencePhoto(
            community_id=cid,
            vk_post_id=photo_id,
            vk_photo_owner_id=-123,
            vk_photo_id=photo_id,
            posted_at=NOW - timedelta(days=age),
            is_style_reference=style,
            archive_discovered=not style,
            width=image.width,
            height=image.height,
            storage_key=key,
            sha256=image.sha256,
            perceptual_hash=image.perceptual_hash,
            embedding=serialize_embedding(vector),
            embedding_model=vector.model,
            embedding_dimensions=vector.dimensions,
        )
        s.add(ref)
        await s.flush()
        return ref, image


async def test_provider_lazy_import_cache_dedup_and_scope(sessions, tmp_path):
    cid, aid = await seed(sessions)
    embedder = FakeVisualEmbedder()
    refs = LocalMediaStorage(tmp_path / "references")
    storage = LocalMediaStorage(tmp_path / "assets")
    async with sessions() as s, s.begin():
        p = await s.get(CommunityContentProfile, cid)
        p.archive_reuse_enabled = True
        other = Community(domain="other")
        s.add(other)
        await s.flush()
        other_id = other.id
    async with (
        VKClient(
            Settings(_env_file=None),
            transport=httpx.MockTransport(lambda r: pytest.fail("Cached archive must not use VK")),
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("Cached archive must not download"))
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            refs,
            embedder,
        )
        provider = VKArchivePhotoProvider(collector)
        ref, image = await cached_reference(sessions, refs, embedder, cid)
        assert await provider.candidates(other_id, now=NOW) == []
        assert len(await provider.candidates(cid, now=NOW)) == 1
        await provider.prepare(ref)
        await provider.prepare(ref)
        assert provider.download_count == provider.embedding_count == 0
        async with sessions() as s:
            assert await s.scalar(select(func.count()).select_from(MediaAsset)) == 0
        visual = VisualLibrary(sessions, storage, embedder)
        planner = CampaignMediaPlanner(
            sessions,
            SearchCache(sessions, FakePhotoProvider(), 24),
            PhotoDownloader(http, PhotoPolicy(), resolver),
            storage,
            Settings(_env_file=None),
            visual=visual,
        )
        pool = await archive_pool(provider, cid, [])
        assert len(pool) == 1 and pool[0].asset is None
        asset, created = await materialize_archive(provider, planner, pool[0], "truck")
        assert created and asset.provider == "vk_archive"
        again, created = await materialize_archive(provider, planner, pool[0], "truck")
        assert not created and again.id == asset.id
        async with sessions() as s:
            assert await s.scalar(select(func.count()).select_from(MediaAsset)) == 1
            alias = await s.get(MediaProviderImport, ("vk_archive", "-123_1"))
            assert alias.source_community_id == cid and alias.source_post_id == 1
        # Another provider with identical bytes links the existing asset.
        async with sessions() as s, s.begin():
            fresh = await s.get(MediaAsset, asset.id)
            fresh.provider = "pixabay"
            fresh.provider_asset_id = "99"
        same, created = await materialize_archive(provider, planner, pool[0], "truck")
        assert not created and same.id == asset.id and same.provider == "pixabay"


async def test_archive_self_exclusion_in_actual_pool_and_preview_no_import(sessions, tmp_path):
    cid, aid = await seed(sessions)
    refs = LocalMediaStorage(tmp_path / "references")
    storage = LocalMediaStorage(tmp_path / "assets")
    embedder = FakeVisualEmbedder()
    async with sessions() as s, s.begin():
        p = await s.get(CommunityContentProfile, cid)
        p.archive_reuse_enabled = True
    ref, image = await cached_reference(sessions, refs, embedder, cid, style=True)
    async with sessions() as s, s.begin():
        r = await s.get(CommunityReferencePhoto, ref.id)
        r.archive_discovered = True
    async with (
        VKClient(
            Settings(_env_file=None), transport=httpx.MockTransport(lambda r: pytest.fail("No VK"))
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            refs,
            embedder,
        )
        archive = VKArchivePhotoProvider(collector)
        visual = VisualLibrary(sessions, storage, embedder)
        pool = await archive_pool(archive, cid, [])
        ranked = await rank_pool(visual, cid, pool, "truck", now=NOW)
        assert (
            len(ranked) == 1
            and ranked[0].score.visual_score is None
            and ranked[0].score.reference_count == 0
        )
        planner = CampaignMediaPlanner(
            sessions,
            SearchCache(sessions, FakePhotoProvider(), 24),
            PhotoDownloader(http, PhotoPolicy(), resolver),
            storage,
            Settings(_env_file=None),
            visual=visual,
        )
        planner.archive = archive
        preview = await photo_preview(planner, visual, cid, PhotoPreviewInput())
        assert (
            preview.mixed_source[0].source == "vk_archive"
            and preview.mixed_source[0].media_asset_id is None
        )
        assert preview.mixed_source[0].reference_id == ref.id
        async with sessions() as s:
            assert await s.scalar(select(func.count()).select_from(MediaAsset)) == 0
            assert await s.scalar(select(func.count()).select_from(CommunityMediaUsage)) == 0


async def test_archive_refreshes_missing_bytes_via_stable_read(sessions, tmp_path):
    cid, aid = await seed(sessions)
    refs = LocalMediaStorage(tmp_path)
    embedder = FakeVisualEmbedder()
    async with sessions() as s, s.begin():
        ref = CommunityReferencePhoto(
            community_id=cid,
            vk_post_id=1,
            vk_photo_owner_id=-123,
            vk_photo_id=1,
            posted_at=NOW - timedelta(days=200),
            source_url="https://evil.test/stale",
            width=1000,
            height=1000,
            is_style_reference=False,
            archive_discovered=True,
        )
        s.add(ref)
        await s.flush()
    calls = []

    def handle(request):
        calls.append(request.url.path)
        assert request.url.path == "/method/wall.getById"
        assert parse_qs(request.content.decode())["posts"] == ["-123_1"]
        p = post(1)
        p["date"] = int(ref.posted_at.timestamp())
        return httpx.Response(200, json={"response": {"items": [p]}})

    def download(request):
        assert request.url.host == "sun9-1.userapi.com"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=image_bytes())

    async with (
        VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handle)) as vk,
        httpx.AsyncClient(transport=httpx.MockTransport(download)) as http,
    ):
        service = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            refs,
            embedder,
        )
        provider = VKArchivePhotoProvider(service)
        row = await provider.prepare(ref)
        assert row.embedding and row.sha256 and row.storage_key
        await provider.prepare(row)
        assert (
            calls == ["/method/wall.getById"]
            and provider.download_count == provider.embedding_count == 1
        )


async def test_archive_profile_api_validation_and_status(client, sessions, tmp_path):
    cid, aid = await seed(sessions)
    response = await client.put(
        f"/api/v1/communities/{cid}/content-profile",
        json={"archive_reuse_enabled": True, "archive_reuse_max_age_days": 540},
    )
    assert response.status_code == 200 and response.json()["archive_reuse_max_age_days"] == 540
    assert (
        await client.put(
            f"/api/v1/communities/{cid}/content-profile", json={"archive_reuse_min_age_days": 540}
        )
    ).status_code == 422
    from dropgrid.photos.reference_routes import collector as dep

    async with (
        VKClient(
            Settings(_env_file=None),
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"response": {"count": 0, "items": []}})
            ),
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            LocalMediaStorage(tmp_path),
            FakeVisualEmbedder(),
        )
        client._transport.app.dependency_overrides[dep] = lambda: collector
        response = await client.post(
            f"/api/v1/communities/{cid}/archive/sync", json={"max_pages": 1}
        )
        assert response.status_code == 200 and response.json()["exhausted"]
        assert (
            await client.post(f"/api/v1/communities/{cid}/archive/sync", json={"max_pages": 51})
        ).status_code == 422


@pytest.mark.parametrize("archive_wins", [True, False])
async def test_real_planner_mixed_winner_lazy_import_and_no_cross_community(
    sessions, tmp_path, archive_wins
):
    from test_photo_integration import prepared

    from dropgrid.db.models import Submission
    from dropgrid.photos.schemas import MediaPlanInput

    campaign_id = await prepared(sessions, count=2)
    async with sessions() as s:
        communities = (await s.scalars(select(Community).order_by(Community.domain))).all()
        cid = communities[0].id
    async with sessions() as s, s.begin():
        s.add(CommunityContentProfile(community_id=cid, archive_reuse_enabled=True))
    refs = LocalMediaStorage(tmp_path / "references")
    storage = LocalMediaStorage(tmp_path / "assets")
    embedder = FakeVisualEmbedder()
    style, _ = await cached_reference(
        sessions, refs, embedder, cid, photo_id=9, style=True, age=1, seed=9
    )
    archive, _ = await cached_reference(sessions, refs, embedder, cid, seed=1)
    vector = VisualEmbedding(embedder.model, 3, (1, 0, 0))
    weak = VisualEmbedding(embedder.model, 3, (-1, 0, 0))
    image = normalize_image(image_bytes(seed=2), PhotoPolicy())
    key = storage.write(image)
    async with sessions() as s, s.begin():
        r = await s.get(CommunityReferencePhoto, style.id)
        r.embedding = serialize_embedding(vector)
        r = await s.get(CommunityReferencePhoto, archive.id)
        r.embedding = serialize_embedding(vector if archive_wins else weak)
        asset = MediaAsset(
            storage_key=key,
            sha256=image.sha256,
            perceptual_hash=image.perceptual_hash,
            width=image.width,
            height=image.height,
            provider="pixabay",
            provider_asset_id="p1",
            category="грузовики",
            tags=[],
            license_code="test-license",
            license_name="Test",
            license_url="https://pixabay.com",
            visual_embedding=serialize_embedding(weak if archive_wins else vector),
            visual_embedding_model=embedder.model,
            visual_embedding_dimensions=3,
        )
        s.add(asset)
        await s.flush()
        pixabay_id = asset.id
    async with (
        VKClient(
            Settings(_env_file=None), transport=httpx.MockTransport(lambda r: pytest.fail("No VK"))
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            refs,
            embedder,
        )
        planner = CampaignMediaPlanner(
            sessions,
            SearchCache(sessions, FakePhotoProvider(), 24),
            PhotoDownloader(http, PhotoPolicy(), resolver),
            storage,
            Settings(_env_file=None),
            visual=VisualLibrary(sessions, storage, embedder),
        )
        planner.archive = VKArchivePhotoProvider(collector)
        await planner.plan(campaign_id, MediaPlanInput(max_reuse_per_asset=1))
    async with sessions() as s:
        own = await s.scalar(select(Submission).where(Submission.community_id == cid))
        other = await s.scalar(select(Submission).where(Submission.community_id != cid))
        archives = (
            await s.scalars(select(MediaAsset).where(MediaAsset.provider == "vk_archive"))
        ).all()
        if archive_wins:
            assert (
                len(archives) == 1
                and own.media_asset_id == archives[0].id
                and other.media_asset_id == pixabay_id
            )
        else:
            assert (
                not archives and own.media_asset_id == pixabay_id and other.media_asset_id is None
            )
        assert await s.scalar(select(func.count()).select_from(CommunityMediaUsage)) == 0
        assert all(a.usage_count == 0 for a in (await s.scalars(select(MediaAsset))).all())


async def test_verified_receipt_records_usage_once(sessions):
    from test_publication_monitor import SUGGESTION
    from test_publication_monitor import seed as receipt_seed

    from dropgrid.db.models import Submission
    from dropgrid.integrations.vk.models import WallPostDetails, WallPostReceipt
    from dropgrid.services.publication import record_suggested_submission

    sid, aid, _, asset_id = await receipt_seed(sessions)
    async with sessions() as s, s.begin():
        row = await s.get(Submission, sid)
        await record_suggested_submission(
            s,
            row,
            account_id=aid,
            receipt=WallPostReceipt(post_id=4),
            suggestion=WallPostDetails(**SUGGESTION),
        )
    async with sessions() as s:
        usage = await s.scalar(select(CommunityMediaUsage))
        assert (
            usage.use_count == 1
            and usage.media_asset_id == asset_id
            and usage.last_submission_id == sid
        )
        assert usage.last_used_at == datetime.fromtimestamp(SUGGESTION["date"], UTC)


async def test_cooldown_excludes_before_archive_download(sessions, tmp_path):
    cid, aid = await seed(sessions)
    async with sessions() as s, s.begin():
        p = await s.get(CommunityContentProfile, cid)
        p.archive_reuse_enabled = True
        s.add(
            CommunityReferencePhoto(
                community_id=cid,
                vk_post_id=1,
                vk_photo_owner_id=-123,
                vk_photo_id=1,
                posted_at=NOW - timedelta(days=200),
                archive_discovered=True,
                is_style_reference=False,
                width=1000,
                height=1000,
            )
        )
        s.add(
            CommunityMediaUsage(
                community_id=cid,
                source_provider="vk_archive",
                source_identity="-123_1",
                first_used_at=NOW - timedelta(days=1),
                last_used_at=NOW - timedelta(days=1),
                use_count=1,
            )
        )
    async with (
        VKClient(
            Settings(_env_file=None),
            transport=httpx.MockTransport(lambda r: pytest.fail("Cooldown must prevent fetch")),
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("Cooldown must prevent download"))
        ) as http,
    ):
        provider = VKArchivePhotoProvider(
            CommunityReferenceCollector(
                sessions,
                vk,
                Tokens(),
                PhotoDownloader(http, VKReferencePolicy(), resolver),
                LocalMediaStorage(tmp_path),
                FakeVisualEmbedder(),
            )
        )
        assert await archive_pool(provider, cid, []) == []


async def test_preview_comparisons_do_not_share_mutable_scores(sessions, tmp_path):
    cid, aid = await seed(sessions)
    refs = LocalMediaStorage(tmp_path / "references")
    storage = LocalMediaStorage(tmp_path / "assets")
    embedder = FakeVisualEmbedder()
    async with sessions() as s, s.begin():
        p = await s.get(CommunityContentProfile, cid)
        p.archive_reuse_enabled = True
    ref, _ = await cached_reference(sessions, refs, embedder, cid, style=True)
    image = normalize_image(image_bytes(seed=15), PhotoPolicy())
    key = storage.write(image)
    vector = await embedder.embed_image(image.data)
    async with sessions() as s, s.begin():
        r = await s.get(CommunityReferencePhoto, ref.id)
        r.archive_discovered = True
        asset = MediaAsset(
            storage_key=key,
            sha256=image.sha256,
            perceptual_hash=image.perceptual_hash,
            width=image.width,
            height=image.height,
            provider="fake",
            provider_asset_id="1",
            tags=["truck"],
            category="грузовики",
            license_code="test-license",
            license_name="Test",
            license_url="https://pixabay.com",
            visual_embedding=serialize_embedding(vector),
            visual_embedding_model=vector.model,
            visual_embedding_dimensions=vector.dimensions,
        )
        s.add(asset)
    async with (
        VKClient(
            Settings(_env_file=None), transport=httpx.MockTransport(lambda r: pytest.fail("No VK"))
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            refs,
            embedder,
        )
        visual = VisualLibrary(sessions, storage, embedder)
        planner = CampaignMediaPlanner(
            sessions,
            SearchCache(sessions, FakePhotoProvider((candidate(),)), 24),
            PhotoDownloader(http, PhotoPolicy(), resolver),
            storage,
            Settings(_env_file=None),
            visual=visual,
        )
        planner.archive = VKArchivePhotoProvider(collector)
        preview = await photo_preview(planner, visual, cid, PhotoPreviewInput())
    assert preview.community_aware[0].visual_score is not None
    assert preview.community_aware[0].final_score <= 1
    assert all(item.visual_score is None for item in preview.mixed_source)


async def test_recent_sync_materializes_metadata_only_archive_reference(sessions, tmp_path):
    cid, aid = await seed(sessions, target=1)
    async with sessions() as s, s.begin():
        s.add(
            CommunityReferencePhoto(
                community_id=cid,
                vk_post_id=1,
                vk_photo_owner_id=-123,
                vk_photo_id=1,
                posted_at=NOW - timedelta(days=200),
                archive_discovered=True,
                is_style_reference=False,
                width=1000,
                height=1000,
            )
        )
    async with (
        VKClient(
            Settings(_env_file=None),
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"response": {"count": 1, "items": [post(1)]}})
            ),
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, headers={"content-type": "image/jpeg"}, content=image_bytes()
                )
            )
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            LocalMediaStorage(tmp_path),
            FakeVisualEmbedder(),
        )
        result = await collector.sync(cid, aid)
    assert (
        result.references_existing == 1
        and result.references_created == 0
        and result.downloads_succeeded == result.references_embedded == 1
    )
    async with sessions() as s:
        row = await s.scalar(select(CommunityReferencePhoto))
        assert (
            row.archive_discovered and row.is_style_reference and row.storage_key and row.embedding
        )
        assert await s.scalar(select(func.count()).select_from(CommunityReferencePhoto)) == 1


async def test_provider_db_strata_metadata_only(sessions, tmp_path):
    cid, _ = await seed(sessions)
    async with sessions() as s, s.begin():
        p = await s.get(CommunityContentProfile, cid)
        p.archive_reuse_enabled = True
        ages = [180 + i / 10 for i in range(100)] + [300] * 20 + [400] * 20 + [540] * 20
        for i, age in enumerate(ages, 1):
            s.add(
                CommunityReferencePhoto(
                    community_id=cid,
                    vk_post_id=i,
                    vk_photo_owner_id=-123,
                    vk_photo_id=i,
                    posted_at=NOW - timedelta(days=age),
                    archive_discovered=True,
                    is_style_reference=False,
                    width=1000,
                    height=1000,
                )
            )
    async with (
        VKClient(
            Settings(_env_file=None),
            transport=httpx.MockTransport(
                lambda r: pytest.fail("Sampling is metadata-only; no VK calls")
            ),
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("Sampling must not download"))
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            LocalMediaStorage(tmp_path),
            FakeVisualEmbedder(),
        )
        provider = VKArchivePhotoProvider(collector)
        first = await provider.candidates(cid, now=NOW)
        second = await provider.candidates(cid, now=NOW)
        assert len(first) == 28
        assert [r.id for r in first] == [r.id for r in second]
        from dropgrid.photos.archive_retrieval import age_band

        bands = [age_band((NOW - r.posted_at).total_seconds() / 86400, 180, 540) for r in first]
        assert [bands.count(i) for i in range(4)] == [7, 7, 7, 7]
        assert all(r.embedding is None and r.storage_key is None for r in first)
        assert len(await provider.candidates(cid, limit=1000, now=NOW)) == 28


async def test_archive_preparation_parallel_limit_and_same_identity_cache_lock(sessions, tmp_path):
    import asyncio

    cid, aid = await seed(sessions)
    embedder = FakeVisualEmbedder()
    storage = LocalMediaStorage(tmp_path)
    async with sessions() as s, s.begin():
        (await s.get(CommunityContentProfile, cid)).archive_reuse_enabled = True
    async with (
        VKClient(
            Settings(_env_file=None),
            transport=httpx.MockTransport(lambda r: pytest.fail("Cached archive must not call VK")),
        ) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("Cached archive must not download"))
        ) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            storage,
            embedder,
        )
        provider = VKArchivePhotoProvider(collector)
        rows = []
        for i in range(1, 10):
            row, _ = await cached_reference(sessions, storage, embedder, cid, photo_id=i, seed=i)
            rows.append(row)
        async with sessions() as s, s.begin():
            row = await s.get(CommunityReferencePhoto, rows[0].id)
            row.embedding = None
        await asyncio.gather(provider.prepare(rows[0]), provider.prepare(rows[0]))
        assert provider.embedding_count == 1
        original = provider.prepare
        active = peak = 0

        async def measured(row):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                return await original(row)
            finally:
                active -= 1

        provider.prepare = measured
        pool = await archive_pool(provider, cid, [])
        assert len(pool) == 9 and 1 < peak <= 3
