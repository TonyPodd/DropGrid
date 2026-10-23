from datetime import timedelta

import httpx
import pytest
from photo_fixtures import image_bytes
from pydantic import SecretStr
from sqlalchemy import func, select
from test_archive_integration import cached_reference
from test_photo_integration import make_planner, prepared, public

from dropgrid.config import Settings
from dropgrid.db.models import (
    CommunityReferencePhoto,
    InstagramPhoto,
    InstagramProfile,
    MediaAsset,
    PhotoSelectionSession,
    Submission,
    utcnow,
)
from dropgrid.integrations.instagram import (
    ApifyInstagramClient,
    InstagramError,
    InstagramGraphClient,
    InstagramImage,
    InstagramPost,
    InstagramReader,
    parse_scraped_post,
    parse_username,
)
from dropgrid.photos.domain import FakePhotoProvider, PhotoPolicy
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.photos.instagram import (
    InstagramPhotoProvider,
    InstagramPolicy,
    InstagramSync,
    centroid,
)
from dropgrid.photos.schemas import MediaPlanInput
from dropgrid.photos.visual import FakeVisualEmbedder, VisualEmbedding, serialize_embedding
from dropgrid.photos.visual_library import VisualLibrary

MODEL = FakeVisualEmbedder.model


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://www.instagram.com/cars.daily/", "cars.daily"),
        ("instagram.com/Cars_Daily?igsh=abc", "cars_daily"),
        ("@cats.of.moscow", "cats.of.moscow"),
        ("plainname", "plainname"),
        ("https://www.instagram.com/p/C1abc/", None),
        ("https://www.instagram.com/reel/C1abc/", None),
        ("https://vk.com/cars", None),
        ("bad name!", None),
        (".dotted", None),
        ("a" * 31, None),
    ],
)
def test_profile_links_become_usernames(value, expected):
    assert parse_username(value) == expected


def graph(handler):
    return InstagramGraphClient(
        SecretStr("secret-token"),
        "17841400000000000",
        "v23.0",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_graph_client_reads_photos_of_a_profile():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "business_discovery": {
                    "username": "cars.daily",
                    "media": {
                        "data": [
                            {
                                "id": "1",
                                "media_type": "IMAGE",
                                "media_url": "https://scontent.cdninstagram.com/1.jpg",
                                "permalink": "https://www.instagram.com/p/A/",
                                "timestamp": "2026-10-01T12:00:00+0000",
                                "caption": "Honda Accord #cars",
                            },
                            {
                                "id": "2",
                                "media_type": "CAROUSEL_ALBUM",
                                "permalink": "https://www.instagram.com/p/B/",
                                "children": {
                                    "data": [
                                        {
                                            "id": "21",
                                            "media_type": "IMAGE",
                                            "media_url": "https://scontent.cdninstagram.com/21.jpg",
                                        },
                                        {"id": "22", "media_type": "VIDEO", "media_url": "x"},
                                    ]
                                },
                            },
                            {
                                "id": "3",
                                "media_type": "VIDEO",
                                "media_url": "https://scontent.cdninstagram.com/3.mp4",
                                "permalink": "https://www.instagram.com/reel/C/",
                            },
                        ]
                    },
                }
            },
        )

    posts = await graph(handler).recent_posts("cars.daily", 50)
    assert [p.post_id for p in posts] == ["1", "2"]
    assert posts[0].images == [InstagramImage("1", "https://scontent.cdninstagram.com/1.jpg")]
    assert posts[0].caption == "Honda Accord #cars" and posts[0].posted_at.year == 2026
    assert [i.media_id for i in posts[1].images] == ["2:21"]
    assert seen[0].url.path == "/v23.0/17841400000000000"
    assert "business_discovery.username(cars.daily)" in seen[0].url.params["fields"]


@pytest.mark.parametrize(
    "code,expected",
    [
        (110, "instagram_profile_unavailable"),
        (100, "instagram_profile_unavailable"),
        (190, "instagram_token_invalid"),
        (4, "instagram_rate_limited"),
        (10, "instagram_permission_missing"),
        (1, "instagram_unavailable"),
    ],
)
async def test_graph_errors_become_fixed_codes(code, expected):
    client = graph(
        lambda r: httpx.Response(400, json={"error": {"code": code, "message": "secret-token"}})
    )
    with pytest.raises(InstagramError) as error:
        await client.recent_posts("someone", 50)
    assert error.value.code == expected and "secret-token" not in str(error.value)


async def test_unconfigured_client_reads_nothing():
    client = InstagramGraphClient(None, None, "v23.0", httpx.AsyncClient())
    assert not client.configured
    with pytest.raises(InstagramError, match="instagram_not_configured"):
        await client.recent_posts("someone", 50)


class FakeGraph:
    def __init__(self, posts=None, error=None, configured=True):
        self.posts, self.error, self.configured = posts or [], error, configured
        self.limits = []

    async def recent_posts(self, username, limit):
        self.limits.append(limit)
        if self.error:
            raise InstagramError(self.error)
        return self.posts


def downloader():
    def handler(request):
        seed = int(request.url.path.rsplit("/", 1)[1].split(".")[0])
        return httpx.Response(
            200, content=image_bytes(seed=seed), headers={"content-type": "image/jpeg"}
        )

    return PhotoDownloader(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), InstagramPolicy(), public
    )


def post(post_id, *seeds):
    return InstagramPost(
        post_id,
        f"https://www.instagram.com/p/{post_id}/",
        utcnow(),
        "Honda Accord",
        [
            InstagramImage(f"{post_id}:{s}", f"https://scontent.cdninstagram.com/v/{s}.jpg")
            for s in seeds
        ],
    )


async def add_profile(sessions, username="cars.daily", **values):
    async with sessions() as db, db.begin():
        profile = InstagramProfile(username=username, **values)
        db.add(profile)
        await db.flush()
        return profile.id


@pytest.mark.integration
async def test_sync_stores_new_photos_once_and_a_centroid(sessions, tmp_path):
    pid = await add_profile(sessions)
    settings = Settings(_env_file=None)
    reader = FakeGraph([post("A", 11), post("B", 12, 13)])
    sync = InstagramSync(
        sessions,
        reader,
        downloader(),
        LocalMediaStorage(tmp_path),
        FakeVisualEmbedder(),
        settings,
    )
    assert await sync.tick() == 1
    async with sessions() as db:
        profile = await db.get(InstagramProfile, pid)
        assets = (
            await db.scalars(select(MediaAsset).where(MediaAsset.provider == "instagram"))
        ).all()
    assert profile.status == "ready" and profile.photo_count == 3 and profile.centroid
    assert {a.creator_name for a in assets} == {"cars.daily"} and len(assets) == 3
    assert all(a.visual_embedding and a.category is None for a in assets)
    assert {a.source_url for a in assets} == {
        "https://www.instagram.com/p/A/",
        "https://www.instagram.com/p/B/",
    }
    # Synced recently: nothing due.
    assert await sync.tick() == 0
    async with sessions() as db, db.begin():
        (await db.get(InstagramProfile, pid)).last_synced_at = utcnow() - timedelta(days=2)
    assert await sync.tick() == 1
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(InstagramPhoto)) == 3
    # The first read takes 50 posts, later reads only the newest.
    assert reader.limits == [50, 12]


@pytest.mark.integration
async def test_unreadable_profile_is_marked_and_unconfigured_sync_idles(sessions, tmp_path):
    pid = await add_profile(sessions)
    settings = Settings(_env_file=None)
    failing = InstagramSync(
        sessions,
        FakeGraph(error="instagram_profile_unavailable"),
        downloader(),
        LocalMediaStorage(tmp_path),
        FakeVisualEmbedder(),
        settings,
    )
    assert await failing.tick() == 1
    async with sessions() as db:
        profile = await db.get(InstagramProfile, pid)
    assert profile.status == "error" and profile.error_code == "instagram_profile_unavailable"
    idle = InstagramSync(
        sessions,
        InstagramGraphClient(None, None, "v23.0", httpx.AsyncClient()),
        downloader(),
        LocalMediaStorage(tmp_path),
        FakeVisualEmbedder(),
        settings,
    )
    async with sessions() as db, db.begin():
        (await db.get(InstagramProfile, pid)).last_synced_at = None
    assert await idle.tick() == 0


def vec(*values):
    return VisualEmbedding(MODEL, 3, values)


async def community_with_wall(sessions, tmp_path, vectors):
    cid = await prepared(sessions, count=1)
    async with sessions() as db:
        row = await db.scalar(select(Submission).where(Submission.campaign_id == cid))
        community_id = row.community_id
    storage = LocalMediaStorage(tmp_path / "refs")
    embedder = FakeVisualEmbedder()
    for i, vector in enumerate(vectors, 1):
        ref, _ = await cached_reference(
            sessions, storage, embedder, community_id, photo_id=i, style=True, age=1, seed=i
        )
        async with sessions() as db, db.begin():
            (await db.get(CommunityReferencePhoto, ref.id)).embedding = serialize_embedding(vector)
    return cid, community_id


async def instagram_photos(sessions, storage, username, vectors, start):
    pid = await add_profile(sessions, username, status="ready")
    async with sessions() as db, db.begin():
        for i, vector in enumerate(vectors):
            image = normalize_image(image_bytes(seed=start + i), PhotoPolicy())
            asset = MediaAsset(
                storage_key=storage.write(image),
                provider="instagram",
                provider_asset_id=f"{username}-{i}",
                creator_name=username,
                license_code="instagram-profile",
                license_name="Publication rights unverified",
                width=image.width,
                height=image.height,
                mime_type="image/jpeg",
                sha256=image.sha256,
                perceptual_hash=image.perceptual_hash,
                visual_embedding=serialize_embedding(vector),
                visual_embedding_model=MODEL,
                visual_embedding_dimensions=3,
            )
            db.add(asset)
            await db.flush()
            db.add(
                InstagramPhoto(
                    profile_id=pid,
                    media_id=f"{username}-{i}",
                    permalink=f"https://www.instagram.com/p/{username}{i}/",
                    media_asset_id=asset.id,
                )
            )
        profile = await db.get(InstagramProfile, pid)
        center = centroid(vectors)
        profile.centroid = serialize_embedding(center)
        profile.centroid_model, profile.centroid_dimensions = MODEL, 3
    return pid


WALL = [vec(1, 0, 0), vec(0.95, 0.05, 0), vec(0.9, 0.1, 0.05)]


@pytest.mark.integration
async def test_bot_picks_fitting_profiles_and_photos(sessions, tmp_path):
    _, community_id = await community_with_wall(sessions, tmp_path, WALL)
    storage = LocalMediaStorage(tmp_path / "assets")
    # Cars profile: two fitting photos and one off-topic photo.
    await instagram_photos(
        sessions, storage, "cars.daily", [vec(0.97, 0.03, 0), vec(0.9, 0.05, 0), vec(0, 0, 1)], 20
    )
    await instagram_photos(sessions, storage, "cats.daily", [vec(0, 1, 0), vec(0.1, 1, 0)], 30)
    provider = InstagramPhotoProvider(VisualLibrary(sessions, storage, FakeVisualEmbedder()))
    found = await provider.candidates(community_id)
    assert [c.asset.creator_name for c in found] == ["cars.daily", "cars.daily"]
    assert all(c.source == "instagram" and c.embedding for c in found)
    # The off-topic photo of the fitting profile is left out too.
    assert {c.asset.provider_asset_id for c in found} == {"cars.daily-0", "cars.daily-1"}


@pytest.mark.integration
async def test_planner_can_choose_an_instagram_photo(sessions, tmp_path):
    cid, community_id = await community_with_wall(sessions, tmp_path, WALL)
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail("No HTTP")))
    planner = make_planner(sessions, tmp_path, FakePhotoProvider(), http)
    embedder = FakeVisualEmbedder()
    planner.visual = VisualLibrary(sessions, planner.storage, embedder)
    planner.instagram = InstagramPhotoProvider(planner.visual)
    await instagram_photos(sessions, planner.storage, "cars.daily", [vec(0.97, 0.03, 0)], 40)
    report = await planner.plan(cid, MediaPlanInput())
    assert report.newly_assigned == 1
    assert report.source_contributions["instagram"]["selected"] == 1
    async with sessions() as db:
        row = await db.scalar(select(Submission).where(Submission.campaign_id == cid))
        asset = await db.get(MediaAsset, row.media_asset_id)
        assert asset.provider == "instagram" and row.photo_source == "instagram"
        assert await db.scalar(select(func.count()).select_from(PhotoSelectionSession)) == 1
    await http.aclose()


@pytest.mark.integration
async def test_profiles_api(client, sessions):
    status = (await client.get("/api/v1/instagram-profiles")).json()
    assert status["configured"] is False and status["profiles"] == []
    added = (
        await client.post(
            "/api/v1/instagram-profiles",
            json={
                "links": "https://www.instagram.com/cars.daily/\n@cats.daily, "
                "https://www.instagram.com/p/post/ cars.daily bad!name"
            },
        )
    ).json()
    assert added == {
        "added": ["cars.daily", "cats.daily"],
        "existing": ["cars.daily"],
        "invalid": ["https://www.instagram.com/p/post/", "bad!name"],
    }
    profiles = (await client.get("/api/v1/instagram-profiles")).json()["profiles"]
    assert {p["username"] for p in profiles} == {"cars.daily", "cats.daily"}
    cars = next(p for p in profiles if p["username"] == "cars.daily")
    assert cars["url"] == "https://www.instagram.com/cars.daily/" and cars["status"] == "pending"
    refreshed = await client.post(f"/api/v1/instagram-profiles/{cars['id']}/sync")
    assert refreshed.json()["status"] == "pending"
    assert (await client.delete(f"/api/v1/instagram-profiles/{cars['id']}")).status_code == 204
    profiles = (await client.get("/api/v1/instagram-profiles")).json()["profiles"]
    assert [p["username"] for p in profiles] == ["cats.daily"]


def test_scraped_posts_keep_photos_and_carousels():
    image = parse_scraped_post(
        {
            "id": "1",
            "type": "Image",
            "url": "https://www.instagram.com/p/A/",
            "displayUrl": "https://scontent.cdninstagram.com/1.jpg",
            "timestamp": "2026-10-01T12:00:00.000Z",
            "caption": "My car",
        }
    )
    assert image.images == [InstagramImage("1", "https://scontent.cdninstagram.com/1.jpg")]
    assert image.posted_at.year == 2026 and image.caption == "My car"
    carousel = parse_scraped_post(
        {
            "id": "2",
            "type": "Sidecar",
            "url": "https://www.instagram.com/p/B/",
            "childPosts": [
                {"id": "21", "type": "Image", "displayUrl": "https://scontent.cdninstagram.com/a"},
                {"id": "22", "type": "Video", "displayUrl": "https://scontent.cdninstagram.com/b"},
            ],
        }
    )
    assert [i.media_id for i in carousel.images] == ["2:21"]
    basic = parse_scraped_post(
        {
            "shortCode": "C3",
            "type": "Sidecar",
            "url": "https://www.instagram.com/p/C3/",
            "images": [
                "https://scontent.cdninstagram.com/x",
                "https://scontent.cdninstagram.com/y",
            ],
        }
    )
    assert [i.media_id for i in basic.images] == ["C3:0", "C3:1"]
    assert (
        parse_scraped_post({"id": "4", "type": "Video", "url": "https://www.instagram.com/p/D/"})
        is None
    )
    assert parse_scraped_post({"error": "not_found"}) is None


async def test_scraper_reads_any_public_profile():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            201,
            json=[
                {
                    "id": "1",
                    "type": "Image",
                    "url": "https://www.instagram.com/p/A/",
                    "displayUrl": "https://scontent.cdninstagram.com/1.jpg",
                }
            ],
        )

    scraper = ApifyInstagramClient(
        SecretStr("apify-token"),
        "apify/instagram-post-scraper",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    posts = await scraper.recent_posts("my.personal", 12)
    assert [p.post_id for p in posts] == ["1"]
    request = seen[0]
    assert request.url.path == "/v2/acts/apify~instagram-post-scraper/run-sync-get-dataset-items"
    assert request.headers["authorization"] == "Bearer apify-token"
    assert "apify-token" not in str(request.url)
    import json

    assert json.loads(request.content) == {"username": ["my.personal"], "resultsLimit": 12}


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (
            201,
            [{"error": "not_found", "errorDescription": "Private"}],
            "instagram_profile_unavailable",
        ),
        (401, {"error": {}}, "instagram_token_invalid"),
        (429, {}, "instagram_rate_limited"),
        (500, {}, "instagram_unavailable"),
    ],
)
async def test_scraper_failures_become_fixed_codes(status, body, expected):
    scraper = ApifyInstagramClient(
        SecretStr("apify-token"),
        "apify~instagram-post-scraper",
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))
        ),
    )
    with pytest.raises(InstagramError) as error:
        await scraper.recent_posts("someone", 12)
    assert error.value.code == expected


async def test_personal_profiles_fall_back_to_the_scraper():
    found = [post("S", 1)]
    official = FakeGraph(error="instagram_profile_unavailable")
    scraper = FakeGraph(found)
    reader = InstagramReader(official, scraper)
    assert reader.configured and await reader.recent_posts("my.personal", 50) == found
    assert official.limits == [50] and scraper.limits == [50]
    # Other official errors are reported, not hidden behind the scraper.
    broken = InstagramReader(FakeGraph(error="instagram_token_invalid"), FakeGraph(found))
    with pytest.raises(InstagramError, match="instagram_token_invalid"):
        await broken.recent_posts("cars.daily", 50)
    # Without the official API every profile goes to the scraper.
    only_scraper = InstagramReader(FakeGraph(configured=False), FakeGraph(found))
    assert await only_scraper.recent_posts("cars.daily", 50) == found
    business = FakeGraph(found)
    unused = FakeGraph()
    assert await InstagramReader(business, unused).recent_posts("cars.daily", 50) == found
    assert unused.limits == []
    nothing = InstagramReader(FakeGraph(configured=False), FakeGraph(configured=False))
    assert not nothing.configured
