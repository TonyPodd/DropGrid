import httpx
import pytest
from photo_fixtures import image_bytes
from pydantic import SecretStr
from sqlalchemy import func, select

from dropgrid.config import Settings
from dropgrid.db.models import (
    Community,
    CommunityMediaUsage,
    MediaAsset,
    PhotoPreviewCache,
    Submission,
)
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import PhotoError, PhotoQueryBuilder, PhotoSearch
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.pinterest import (
    ApifyPinterestBackend,
    PinterestPhotoProvider,
    PinterestPolicy,
    pin_candidate,
)
from dropgrid.photos.pinterest_preview import MATERIALIZATION_LIMIT, PinterestPreview
from dropgrid.photos.visual import FakeVisualEmbedder
from dropgrid.photos.visual_library import VisualLibrary


def pin(i=1):
    identity = str(123450000 + i)
    return {
        "id": identity,
        "url": f"https://www.pinterest.com/pin/{identity}/",
        "imageUrl": f"https://i.pinimg.com/originals/{i}.jpg",
        "width": 1000,
        "height": 1000,
        "title": "Honda Accord car",
        "description": "street exterior",
        "alt": "sedan",
    }


async def test_disabled_provider():
    assert not Settings(_env_file=None).pinterest_search_enabled
    with pytest.raises(PhotoError) as error:
        await PinterestPhotoProvider(None).search(PhotoSearch(query="Honda Accord"))
    assert error.value.code == "pinterest_backend_unavailable"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "bad"),
        ("url", "http://www.pinterest.com/pin/123450001/"),
        ("url", "https://www.pinterest.com.evil.test/pin/123450001/"),
        ("url", "https://u:p@pinterest.com/pin/123450001/"),
        ("imageUrl", "https://127.0.0.1/pin.jpg"),
        ("imageUrl", "https://i.pinimg.com.evil.test/pin.jpg"),
        ("imageUrl", "https://i.pinimg.com/photo?access_token=secret"),
        ("width", 0),
        ("height", 10000000),
        ("width", True),
    ],
)
def test_reject_bad_pin(field, value):
    row = pin()
    row[field] = value
    with pytest.raises((PhotoError, ValueError)):
        pin_candidate(row, 0)


async def test_apify_bearer_contract_no_retries_and_safe_errors():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer fixture-secret"
        assert "fixture-secret" not in str(request.url)
        if request.method == "POST":
            assert request.url.path == "/v2/actors/fixture~search/runs"
            assert b'"searchQueries":["honda accord"]' in request.content
            return httpx.Response(
                201, json={"data": {"status": "SUCCEEDED", "defaultDatasetId": "abc123"}}
            )
        return httpx.Response(200, json=[pin(), pin()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = PinterestPhotoProvider(
            ApifyPinterestBackend(SecretStr("fixture-secret"), "fixture/search", client)
        )
        result = await provider.search(PhotoSearch(query="Honda Accord"))
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.title == "Honda Accord car" and c.alt_text == "sedan"
        assert not c.publication_eligible and c.provider == "pinterest"
    assert len(requests) == 2
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(403, text="sensitive"))
    ) as client:
        with pytest.raises(PhotoError) as error:
            await ApifyPinterestBackend(SecretStr("fixture-secret"), "fixture", client).search(
                "car", 25
            )
        assert error.value.code == "pinterest_search_unavailable" and "sensitive" not in str(
            error.value
        )


@pytest.mark.integration
async def test_multiple_queries_bounded_materialization_cached_and_no_campaign_side_effects(
    sessions, tmp_path
):
    class Backend:
        def __init__(self):
            self.calls = []

        async def search(self, query, limit):
            self.calls.append(query)
            assert limit == 25
            offset = len(self.calls) * 20
            return [pin(offset + i) for i in range(25)]

    backend = Backend()
    embedder = FakeVisualEmbedder()
    visual = VisualLibrary(sessions, LocalMediaStorage(tmp_path / "assets"), embedder)
    async with sessions() as session, session.begin():
        community = Community(domain="fixture-honda")
        session.add(community)
        await session.flush()
        cid = community.id
    downloads = []

    async def resolver(host):
        return ["8.8.8.8"]

    def handle(request):
        assert (
            request.headers.get("authorization") is None and request.headers.get("cookie") is None
        )
        downloads.append(str(request.url))
        return httpx.Response(
            200, content=image_bytes(seed=len(downloads)), headers={"Content-Type": "image/jpeg"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        preview = PinterestPreview(
            sessions,
            SearchCache(sessions, PinterestPhotoProvider(backend)),
            PhotoDownloader(client, PinterestPolicy(), resolver),
            LocalMediaStorage(tmp_path / "previews"),
            visual,
        )
        plan = PhotoQueryBuilder().build("ХОНДА АККОРД")
        ranked, retrieved, queries, warnings = await preview.compare(cid, plan)
        assert len(backend.calls) == 2 and retrieved == 40
        assert len(downloads) == embedder.calls == MATERIALIZATION_LIMIT
        assert len(queries) == 40 and not warnings
        await preview.compare(cid, plan)
        assert len(downloads) == embedder.calls == MATERIALIZATION_LIMIT
        assert len(backend.calls) == 2
    async with sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(PhotoPreviewCache))
            == MATERIALIZATION_LIMIT
        )
        for model in (MediaAsset, CommunityMediaUsage, Submission):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    assert all(c.preview_id and not c.asset and not c.photo.publication_eligible for c in ranked)


@pytest.mark.integration
async def test_disabled_preview_api_reports_inactive(client, sessions):
    async with sessions() as session, session.begin():
        row = Community(domain="disabled-preview")
        session.add(row)
        await session.flush()
        cid = row.id
    result = await client.post(
        f"/api/v1/communities/{cid}/photo-preview", json={"include_archive": False}
    )
    assert result.status_code == 200
    body = result.json()
    assert body["pinterest_status"] == "disabled" and not body["pinterest"]
    assert not body["visual_engine"]["active"]
    assert body["visual_engine"]["reason_if_inactive"] == "model_disabled"


@pytest.mark.integration
async def test_campaign_import_fails_closed_without_network(sessions, tmp_path):
    from dropgrid.photos.domain import FakePhotoProvider, PhotoPolicy
    from dropgrid.photos.planner import CampaignMediaPlanner

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
    ) as client:
        planner = CampaignMediaPlanner(
            sessions,
            SearchCache(sessions, FakePhotoProvider()),
            PhotoDownloader(client, PhotoPolicy()),
            LocalMediaStorage(tmp_path),
            Settings(_env_file=None),
        )
        assert await planner._import(pin_candidate(pin(), 0), "honda", False) == (
            None,
            False,
            "publication_ineligible",
        )
        assert await planner._import(
            pin_candidate(pin(), 0).model_copy(update={"provider": "fake"}), "honda", False
        ) == (None, False, "publication_ineligible")
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(MediaAsset)) == 0
