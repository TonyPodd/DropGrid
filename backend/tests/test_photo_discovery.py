from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from photo_fixtures import candidate, image_bytes
from sqlalchemy import select
from test_photo_integration import make_planner, prepared, public
from test_pinterest import pin

from dropgrid.config import Settings
from dropgrid.db.models import Community, MediaAsset, MediaProviderImport, Submission
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.discovery import discover
from dropgrid.photos.domain import FakePhotoProvider, PhotoPolicy, PhotoQueryBuilder
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.pinterest import PinterestPhotoProvider, PinterestPolicy, pin_candidate
from dropgrid.photos.pinterest_preview import PinterestPreview
from dropgrid.photos.pool import PoolCandidate, deduplicate_pool
from dropgrid.photos.schemas import MediaPlanInput
from dropgrid.photos.visual import FakeVisualEmbedder
from dropgrid.photos.visual_library import VisualLibrary


@pytest.mark.parametrize(
    "count,warning,expected",
    [
        (16, None, "not_needed"),
        (3, None, "primary_insufficient"),
        (0, "pinterest_search_unavailable", "primary_unavailable"),
    ],
)
async def test_primary_threshold_controls_fallback(count, warning, expected):
    class Primary:
        visual = SimpleNamespace(embedder=None)
        last_retrieval = None

        async def compare(self, cid, plan):
            return (
                [PoolCandidate(pin_candidate(pin(i), i), "pinterest") for i in range(count)],
                count,
                {},
                [warning] if warning else [],
            )

    calls = []

    class Cache:
        provider = FakePhotoProvider()

        async def search(self, search):
            calls.append(search)
            return (candidate(),), False

    result = await discover(
        Primary(),
        Cache(),
        PhotoQueryBuilder().build("Honda Accord"),
        uuid4(),
        Settings(_env_file=None),
        PhotoPolicy(),
    )
    assert result.fallback_reason == expected
    assert bool(calls) == (count < 16)
    if warning:
        assert "pinterest_fallback_used" in result.warnings


async def test_small_optional_diversity_is_bounded():
    class Primary:
        visual = SimpleNamespace(embedder=None)
        last_retrieval = None

        async def compare(self, cid, plan):
            return (
                [PoolCandidate(pin_candidate(pin(i), i), "pinterest") for i in range(24)],
                24,
                {},
                [],
            )

    class Cache:
        provider = FakePhotoProvider()

        async def search(self, search):
            return tuple(candidate(i) for i in range(20)), False

    result = await discover(
        Primary(),
        Cache(),
        PhotoQueryBuilder().build("Honda Accord"),
        uuid4(),
        Settings(_env_file=None, photo_fallback_diversity_candidates=4),
        PhotoPolicy(),
    )
    assert result.fallback_reason == "diversity"
    assert len(result.fallback.items) == 4 and result.fallback.requests == 1


def test_cross_provider_exact_and_perceptual_dedup_keeps_source_neutral_pool():
    pool = [
        PoolCandidate(candidate(i, provider=provider), provider, sha256="same")
        for i, provider in enumerate(["pinterest", "pixabay", "library", "vk_archive"])
    ]
    assert len(deduplicate_pool(pool)) == 1
    assert [p.identity for p in deduplicate_pool(pool)] == [
        p.identity for p in deduplicate_pool(pool[::-1])
    ]


@pytest.mark.integration
@pytest.mark.parametrize("publication", [False, True])
async def test_real_planner_pinterest_boundary_cached_import_and_provenance(
    sessions, tmp_path, publication
):
    campaign_id = await prepared(sessions, count=1, category="Honda Accord")
    async with sessions() as session:
        community_id = await session.scalar(select(Submission.community_id))

    class Backend:
        async def search(self, query, limit):
            return [dict(pin(), query=query)]

    fallback = FakePhotoProvider()
    downloads = []

    def handle(request):
        downloads.append(request.url.host)
        return httpx.Response(200, content=image_bytes(), headers={"content-type": "image/jpeg"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        planner = make_planner(
            sessions,
            tmp_path,
            fallback,
            client,
            pinterest_publication_enabled=publication,
            photo_primary_min_candidates=1,
        )
        visual = VisualLibrary(sessions, planner.storage, FakeVisualEmbedder())
        planner.visual = visual
        planner.pinterest_preview = PinterestPreview(
            sessions,
            SearchCache(sessions, PinterestPhotoProvider(Backend())),
            PhotoDownloader(client, PinterestPolicy(), public),
            LocalMediaStorage(tmp_path / "pins"),
            visual,
        )
        result = await planner.plan(campaign_id, MediaPlanInput())
        assert result.newly_assigned == int(publication)
        assert bool(fallback.calls) != publication
        assert downloads == ["i.pinimg.com"]
        async with sessions() as session:
            submission = await session.scalar(select(Submission))
            assets = (await session.scalars(select(MediaAsset))).all()
            if publication:
                assert len(assets) == 1 and submission.media_asset_id == assets[0].id
                provenance = await session.scalar(select(MediaProviderImport))
                assert provenance.provider == "pinterest"
                assert provenance.provider_asset_id == "123450001"
                assert provenance.source_url == pin()["url"]
                assert provenance.image_source_url == pin()["imageUrl"]
                assert provenance.retrieval_query == "honda accord"
                assert provenance.license_code == assets[0].license_code == "unverified-public-pin"
                assert planner.eligible(assets[0])
                planner.settings.pinterest_publication_enabled = False
                assert not planner.eligible(assets[0])
            else:
                assert not assets and submission.media_asset_id is None
        assert community_id


@pytest.mark.integration
async def test_feedback_crud_is_metadata_only(client, sessions):
    async with sessions() as session, session.begin():
        row = Community(domain="feedback")
        session.add(row)
        await session.flush()
        cid = row.id
    url = f"/api/v1/communities/{cid}/photo-feedback"
    body = {"provider": "pinterest", "source_identity": "123450001", "rating": "like"}
    assert (await client.put(url, json=body)).status_code == 200
    assert (await client.put(url, json={**body, "rating": "dislike"})).json()["rating"] == "dislike"
    rows = (await client.get(url)).json()
    assert len(rows) == 1 and rows[0]["rating"] == "dislike"
    assert (await client.put(url, json={**body, "rating": "bad"})).status_code == 422
    assert (
        await client.delete(url, params={"provider": "pinterest", "source_identity": "123450001"})
    ).status_code == 204
    assert (await client.get(url)).json() == []


@pytest.mark.integration
async def test_library_remains_in_global_pool_with_sufficient_pinterest(sessions, tmp_path):
    from dropgrid.photos.preview import photo_preview
    from dropgrid.photos.reference_schemas import PhotoPreviewInput

    async with sessions() as s, s.begin():
        community = Community(domain="library-with-pins", category="Honda Accord")
        s.add(community)
        await s.flush()
        cid = community.id

    class Backend:
        async def search(self, query, limit):
            return [dict(pin(), query=query)]

    def handle(request):
        return httpx.Response(
            200,
            content=image_bytes(seed=2 if request.url.host == "i.pinimg.com" else 1),
            headers={"content-type": "image/jpeg"},
        )

    fallback = FakePhotoProvider()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        planner = make_planner(sessions, tmp_path, fallback, client, photo_primary_min_candidates=1)
        visual = VisualLibrary(sessions, planner.storage, FakeVisualEmbedder())
        asset, _, _ = await planner._import(candidate(), "honda accord", False)
        planner.pinterest_preview = PinterestPreview(
            sessions,
            SearchCache(sessions, PinterestPhotoProvider(Backend())),
            PhotoDownloader(client, PinterestPolicy(), public),
            LocalMediaStorage(tmp_path / "pins"),
            visual,
        )
        preview = await photo_preview(
            planner, visual, cid, PhotoPreviewInput(include_archive=False)
        )
        assert not fallback.calls and preview.pixabay_requests == 0
        assert preview.pixabay_status == "not_needed"
        assert {row.source for row in preview.best_matches} == {"library", "pinterest"}
        assert any(row.media_asset_id == asset.id for row in preview.best_matches)
        assert preview.source_contributions["library"]["retrieved"] == 1
        assert preview.source_contributions["pinterest"]["materialized"] == 1
        assert preview.source_contributions["pinterest"]["selected"] == 0
