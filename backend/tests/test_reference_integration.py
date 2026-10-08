from collections import Counter
from datetime import UTC, datetime
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from photo_fixtures import candidate, image_bytes
from pydantic import SecretStr
from sqlalchemy import func, select

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Community,
    CommunityContentProfile,
    CommunityReferencePhoto,
    MediaAsset,
)
from dropgrid.integrations.vk.client import VKClient
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import FakePhotoProvider, PhotoPolicy
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.photos.planner import CampaignMediaPlanner, assign_assets
from dropgrid.photos.preview import photo_preview
from dropgrid.photos.reference_schemas import PhotoPreviewInput
from dropgrid.photos.references import CommunityReferenceCollector, VKReferencePolicy
from dropgrid.photos.visual import FakeVisualEmbedder, serialize_embedding
from dropgrid.photos.visual_library import VisualLibrary

pytestmark = pytest.mark.integration


def post(id, photo=True):
    return {
        "id": id,
        "owner_id": -123,
        "from_id": -123,
        "post_type": "post",
        "date": 1791483366,
        "attachments": [
            {
                "type": "photo",
                "photo": {
                    "id": id,
                    "owner_id": -123,
                    "sizes": [
                        {
                            "width": 1000,
                            "height": 1000,
                            "url": f"https://sun9-1.userapi.com/{id}.jpg",
                        }
                    ],
                },
            }
        ]
        if photo
        else [],
    }


class Tokens:
    async def get_token(self, account_id):
        return SecretStr("mock-token")


async def seed(sessions, target=3):
    async with sessions() as s, s.begin():
        a = Account(name="Test", vk_user_id=1, encrypted_access_token="fake")
        c = Community(domain="club123", vk_group_id=123, category="грузовики")
        s.add_all([a, c])
        await s.flush()
        s.add(CommunityContentProfile(community_id=c.id, reference_target_count=target))
        return c.id, a.id


async def resolver(host):
    return ["8.8.8.8"]


@pytest.mark.parametrize("scenario", ["target", "scan_bound", "empty"])
async def test_reference_pagination(sessions, tmp_path, scenario):
    cid, aid = await seed(sessions, target=3)
    seen = []
    downloads = []
    embedder = FakeVisualEmbedder()
    posts = (
        [post(i, i % 2 == 0) for i in range(1, 21)]
        if scenario == "target"
        else [post(i, False) for i in range(1, 601)]
        if scenario == "scan_bound"
        else []
    )

    def handle(request):
        assert request.url.path == "/method/wall.get"
        form = parse_qs(request.content.decode())
        assert form["owner_id"] == ["-123"] and form["filter"] == ["owner"]
        offset = int(form["offset"][0])
        seen.append(offset)
        # Deliberately short API pages, not a blanket first 100-post fetch.
        page = posts[offset : offset + (4 if scenario == "target" else 100)]
        return httpx.Response(200, json={"response": {"count": len(posts), "items": page}})

    def image(request):
        downloads.append(request.url.path)
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=image_bytes(seed=int(request.url.path[1:-4])),
        )

    async with (
        VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handle)) as vk,
        httpx.AsyncClient(transport=httpx.MockTransport(image)) as http,
    ):
        collector = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            LocalMediaStorage(tmp_path),
            embedder,
        )
        result = await collector.sync(cid)
        if scenario == "target":
            assert (
                result.posts_scanned == 6
                and result.references_created == 3
                and result.references_embedded == 3
            )
            repeat = await collector.sync(cid)
            assert repeat.references_existing == 3 and repeat.references_created == 0
            assert len(downloads) == 3 and embedder.calls == 3
            async with sessions() as s, s.begin():
                rows = (await s.scalars(select(CommunityReferencePhoto))).all()
                rows[0].embedding = None
            assert await collector.backfill(cid) == 1
            assert len(downloads) == 3
            assert seen == [0, 4, 0, 4]
        elif scenario == "scan_bound":
            assert result.posts_scanned == 400 and seen == [0, 100, 200, 300]
        else:
            assert result.posts_scanned == 0 and not downloads
    async with sessions() as s:
        assert (await s.scalar(select(func.count()).select_from(MediaAsset))) == 0


async def test_candidate_embedding_cache_and_preview(sessions, tmp_path):
    cid, aid = await seed(sessions)
    embedder = FakeVisualEmbedder()
    storage = LocalMediaStorage(tmp_path)
    image = normalize_image(image_bytes(), PhotoPolicy())
    key = storage.write(image)
    async with sessions() as s, s.begin():
        asset = MediaAsset(
            storage_key=key,
            sha256=image.sha256,
            perceptual_hash=image.perceptual_hash,
            width=1000,
            height=1000,
            provider="fake",
            provider_asset_id="1",
            category="грузовики",
            tags=["truck"],
            license_code="test-license",
            license_name="test",
            license_url="https://pixabay.com/service/license-summary/",
        )
        s.add(asset)
        await s.flush()
        asset_id = asset.id
        vector = await embedder.embed_image(image.data)
        s.add(
            CommunityReferencePhoto(
                community_id=cid,
                vk_post_id=1,
                vk_photo_owner_id=-123,
                vk_photo_id=1,
                posted_at=datetime.now(UTC),
                embedding=serialize_embedding(vector),
                embedding_model=vector.model,
                embedding_dimensions=vector.dimensions,
            )
        )
    library = VisualLibrary(sessions, storage, embedder)
    first = await library.asset_embedding(asset)
    second = await library.asset_embedding(asset)
    assert first == second and embedder.calls == 2
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("Known candidate not downloaded"))
    ) as http:
        planner = CampaignMediaPlanner(
            sessions,
            SearchCache(sessions, FakePhotoProvider((candidate(),)), 24),
            PhotoDownloader(http, PhotoPolicy(), resolver),
            storage,
            Settings(_env_file=None),
            visual=library,
        )
        preview = await photo_preview(planner, library, cid, PhotoPreviewInput(candidate_limit=1))
    assert preview.community_aware[0].visual_score == pytest.approx(1)
    assert preview.community_aware[0].media_asset_id == asset_id
    assert embedder.calls == 2
    async with sessions() as s:
        assert (await s.get(MediaAsset, asset_id)).usage_count == 0


async def test_profile_api_and_sync_endpoint(client, sessions, tmp_path):
    from dropgrid.photos.reference_routes import collector as dep

    cid, aid = await seed(sessions)
    response = await client.get(f"/api/v1/communities/{cid}/content-profile")
    assert response.status_code == 200 and response.json()["archive_reuse_enabled"] is False
    response = await client.put(
        f"/api/v1/communities/{cid}/content-profile",
        json={"desired_content": "dogs", "reference_target_count": 2},
    )
    assert response.status_code == 200 and response.json()["desired_content"] == "dogs"
    assert (
        await client.put(
            f"/api/v1/communities/{cid}/content-profile", json={"reference_target_count": 301}
        )
    ).status_code == 422

    def handle(request):
        return httpx.Response(200, json={"response": {"count": 0, "items": []}})

    async with (
        VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handle)) as vk,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
        ) as http,
    ):
        service = CommunityReferenceCollector(
            sessions,
            vk,
            Tokens(),
            PhotoDownloader(http, VKReferencePolicy(), resolver),
            LocalMediaStorage(tmp_path),
            FakeVisualEmbedder(),
        )
        client._transport.app.dependency_overrides[dep] = lambda: service
        response = await client.post(f"/api/v1/communities/{cid}/references/sync", json={})
    assert response.status_code == 200 and response.json()["posts_scanned"] == 0
    assert (await client.get(f"/api/v1/communities/{cid}/references")).json()["total"] == 0
    assert (await client.get(f"/api/v1/communities/{uuid4()}/content-profile")).status_code == 404
    assert "mock-token" not in response.text


async def test_assignment_uses_visual_score_but_preserves_hard_reuse():
    a, b = (
        MediaAsset(id=uuid4(), width=1000, height=1000, usage_count=0),
        MediaAsset(id=uuid4(), width=1000, height=1000, usage_count=0),
    )
    ids = [uuid4() for _ in range(3)]
    counts = Counter()
    result = assign_assets(
        ids, [a, b], counts, 1, Counter(), {s: {a.id: 0.2, b.id: 0.9} for s in ids}
    )
    assert result[ids[0]] == b.id and result[ids[1]] == a.id and ids[2] not in result


async def test_existing_community_without_profile_has_defaults(client, sessions):
    async with sessions() as s, s.begin():
        c = Community(domain="legacy-community")
        s.add(c)
        await s.flush()
        cid = c.id
    response = await client.get(f"/api/v1/communities/{cid}/content-profile")
    assert response.status_code == 200
    data = response.json()
    assert data["reference_target_count"] == 100
    assert data["archive_reuse_enabled"] is False
    assert data["archive_reuse_min_age_days"] == 180
    assert data["reference_count"] == 0
    assert data["created_at"] is None
    async with sessions() as s:
        assert await s.get(CommunityContentProfile, cid) is None
    response = await client.put(f"/api/v1/communities/{cid}/content-profile", json={})
    assert response.status_code == 200 and response.json()["created_at"] is not None
