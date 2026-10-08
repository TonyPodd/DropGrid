import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from photo_fixtures import candidate, image_bytes

from dropgrid.photos.domain import PhotoError, PhotoRanker, PhotoSearch
from dropgrid.photos.download import PhotoDownloader, validate_url
from dropgrid.photos.images import normalize_image
from dropgrid.photos.references import (
    VKReferencePolicy,
    eligible_for_archive_reuse,
    representative_photo,
)
from dropgrid.photos.visual import (
    CommunityVisualRanker,
    VisualEmbedding,
    VisualScore,
    cosine_similarity,
    deserialize_embedding,
    rank_photo,
    serialize_embedding,
)


@pytest.mark.parametrize(
    "values,dimensions",
    [((0.0, 0.0), 2), ((math.nan, 1), 2), ((math.inf, 1), 2), ((1, 2), 3), ((1,), 0)],
)
def test_invalid_vectors(values, dimensions):
    with pytest.raises(PhotoError):
        VisualEmbedding("test", dimensions, values)


def test_embedding_roundtrip_and_model_mismatch():
    a = VisualEmbedding("test", 2, (3, 4))
    blob = serialize_embedding(a)
    assert len(blob) == 8
    b = deserialize_embedding(blob, "test", 2)
    assert cosine_similarity(a, b) == pytest.approx(1)
    assert cosine_similarity(a, VisualEmbedding("test", 2, (-4, 3))) == pytest.approx(0, abs=1e-6)
    with pytest.raises(PhotoError):
        cosine_similarity(a, VisualEmbedding("other", 2, (3, 4)))
    with pytest.raises(PhotoError):
        deserialize_embedding(blob, "test", 3)
    with pytest.raises(PhotoError):
        deserialize_embedding(b"\0" * 8, "test", 2)


def test_top_five_and_incompatible_references():
    a = VisualEmbedding("test", 2, (1, 0))
    good = [VisualEmbedding("test", 2, (1, 0)) for _ in range(5)]
    score = CommunityVisualRanker().score(a, good + [VisualEmbedding("test", 2, (-1, 0))] * 30)
    assert score.top_k_mean == 1 and score.reference_count == 35
    small = CommunityVisualRanker().score(a, [a, VisualEmbedding("test", 2, (0, 1))])
    assert small.top_k_mean == 0.5
    assert CommunityVisualRanker().score(a, []).top_k_mean is None
    assert (
        CommunityVisualRanker().score(a, [VisualEmbedding("other", 2, (1, 0))]).reference_count == 0
    )


def test_ranking_fallback_and_named_visual_signal():
    c = candidate()
    search = PhotoSearch(query="truck highway")
    assert rank_photo(c, search, VisualScore(None, None, 0)).final_score == PhotoRanker().score(
        c, search
    )
    strong = rank_photo(c, search, VisualScore(1, 1, 5))
    weak = rank_photo(c, search, VisualScore(-1, -1, 5))
    assert strong.final_score - weak.final_score == pytest.approx(0.6)
    assert 0 <= strong.final_score <= 1


@pytest.mark.parametrize(
    "url",
    [
        "http://sun9-1.userapi.com/x.jpg",
        "https://userapi.com.evil.test/x",
        "https://evil.test/x",
        "https://userapi.com:444/x",
        "https://u:p@userapi.com/x",
        "https://userapi.com/x?access_token=secret",
        "https://127.0.0.1/x",
    ],
)
def test_reject_reference_urls(url):
    with pytest.raises(PhotoError):
        validate_url(url, VKReferencePolicy())


def post(id=1, photo=True):
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
                            "width": 300,
                            "height": 300,
                            "url": "https://sun9-1.userapi.com/small.jpg",
                        },
                        {
                            "width": 1000,
                            "height": 1000,
                            "url": "https://sun9-1.userapi.com/large.jpg",
                        },
                    ],
                },
            }
        ]
        if photo
        else [],
    }


def test_representative_is_primary_largest_and_skips_reposts():
    p = post()
    p["attachments"] += post(2)["attachments"]
    result = representative_photo(p, 123, VKReferencePolicy())
    assert result.photo_id == 1 and result.url.endswith("large.jpg")
    assert representative_photo({**p, "copy_history": [{}]}, 123, VKReferencePolicy()) is None
    assert representative_photo({**p, "post_type": "suggest"}, 123, VKReferencePolicy()) is None
    assert representative_photo(post(photo=False), 123, VKReferencePolicy()) is None


@pytest.mark.parametrize(
    "days,enabled,expected",
    [(179, True, False), (180, True, True), (181, True, True), (365, False, False)],
)
def test_archive_age_boundary(days, enabled, expected):
    now = datetime.now(UTC)
    assert eligible_for_archive_reuse(now - timedelta(days=days), now, 180, enabled) == expected
    assert not eligible_for_archive_reuse(now, now, 0, True, valid=False)
    assert not eligible_for_archive_reuse(now + timedelta(seconds=1), now, 0, True)


async def test_safe_reference_download_bounds_and_redirect_validation():
    async def resolver(host):
        return ["8.8.8.8"]

    def handle(request):
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(302, headers={"location": "http://evil.test/x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(PhotoError):
            await PhotoDownloader(client, VKReferencePolicy(), resolver).download(
                "https://sun9-1.userapi.com/x"
            )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                headers={"content-type": "image/jpeg", "content-length": "999999999"},
                content=b"x",
            )
        )
    ) as client:
        with pytest.raises(PhotoError):
            await PhotoDownloader(client, VKReferencePolicy(), resolver).download(
                "https://sun9-1.userapi.com/x"
            )
    with pytest.raises(PhotoError):
        normalize_image(b"bad image", VKReferencePolicy())
    with pytest.raises(PhotoError):
        normalize_image(image_bytes(), replace(VKReferencePolicy(), max_pixels=10))


async def test_cdn_cookies_and_client_auth_never_replayed():
    from dropgrid.photos.domain import PhotoPolicy

    seen = []

    def handler(request):
        assert "cookie" not in request.headers and "authorization" not in request.headers
        seen.append(request.url.path)
        if request.url.path == "/redirect":
            return httpx.Response(
                302, headers={"location": "/image.jpg", "set-cookie": "cdn=private; Path=/; Secure"}
            )
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg", "set-cookie": "cdn=private; Path=/; Secure"},
            content=b"image-bytes",
        )

    async def resolver(host):
        return ["8.8.8.8"]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=("private", "private"),
        cookies={"initial": "private"},
    ) as client:
        downloader = PhotoDownloader(client, PhotoPolicy(), resolver)
        assert await downloader.download("https://cdn.pixabay.com/redirect") == b"image-bytes"
        assert await downloader.download("https://cdn.pixabay.com/image.jpg") == b"image-bytes"
    assert seen == ["/redirect", "/image.jpg", "/image.jpg"]
