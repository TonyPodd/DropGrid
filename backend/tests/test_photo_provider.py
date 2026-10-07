import httpx
import pytest
from pydantic import SecretStr

from dropgrid.photos.domain import PhotoError, PhotoSearch
from dropgrid.photos.pixabay import PixabayPhotoProvider


def hit():
    return {
        "id": 123,
        "pageURL": "https://pixabay.com/photos/truck-123/",
        "largeImageURL": "https://cdn.pixabay.com/photo/truck.jpg",
        "type": "photo",
        "tags": "truck, highway",
        "imageWidth": 4000,
        "imageHeight": 3000,
        "imageSize": 5000000,
        "user_id": 1,
        "user": "Creator",
        "likes": 20,
    }


async def test_search_mapping_ru_pagination_optional_and_headers():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"hits": [hit()]},
            headers={
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "99",
                "X-RateLimit-Reset": "59",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = PixabayPhotoProvider(SecretStr("secret-test-key"), client)
        result = await provider.search(PhotoSearch(query="грузовик дорога", page=2))
    params = requests[0].url.params
    assert params["q"] == "грузовик дорога" and params["lang"] == "ru" and params["page"] == "2"
    assert (
        params["image_type"] == "photo"
        and params["safesearch"] == "true"
        and params["orientation"] == "all"
    )
    photo = result.candidates[0]
    assert photo.provider_asset_id == "123" and photo.downloads == photo.views == 0
    assert photo.creator_url == "https://pixabay.com/users/Creator-1/"
    assert photo.candidate_download_url.endswith("truck.jpg")
    assert result.rate_limit == 100 and result.remaining == 99 and result.reset == 59


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "provider_authentication_failed"),
        (403, "provider_authentication_failed"),
        (429, "provider_rate_limited"),
        (500, "provider_unavailable"),
        (503, "provider_unavailable"),
        (302, "provider_unavailable"),
    ],
)
async def test_safe_http_errors(status, code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, text="RAW-SECRET", headers={"X-RateLimit-Reset": "20"})
        )
    ) as client:
        provider = PixabayPhotoProvider(SecretStr("secret-test-key"), client)
        with pytest.raises(PhotoError) as exc:
            await provider.search(PhotoSearch(query="truck"))
        assert str(exc.value) == code
        if status == 429:
            with pytest.raises(PhotoError):
                await provider.search(PhotoSearch(query="cat"))


@pytest.mark.parametrize(
    "body", ["bad JSON", "[]", "{}", '{"hits":"bad"}', '{"hits":[],"key":"secret-test-key"}']
)
async def test_invalid_response(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))
    ) as client:
        with pytest.raises(PhotoError, match="provider_invalid_response"):
            await PixabayPhotoProvider(SecretStr("secret-test-key"), client).search(
                PhotoSearch(query="truck")
            )


async def test_missing_key_empty_results_and_timeout():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"hits": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PhotoError, match="provider_unavailable"):
            await PixabayPhotoProvider(None, client).search(PhotoSearch(query="truck"))
        assert not calls
        assert not (
            await PixabayPhotoProvider(SecretStr("secret-test-key"), client).search(
                PhotoSearch(query="truck")
            )
        ).candidates

    def timeout(request):
        raise httpx.ReadTimeout("raw secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(PhotoError, match="provider_unavailable"):
            await PixabayPhotoProvider(SecretStr("secret-test-key"), client).search(
                PhotoSearch(query="truck")
            )


async def test_key_and_raw_body_never_logged(caplog):
    import logging

    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(403, text="RAW-BODY-secret-test-key")
        )
    ) as client:
        with pytest.raises(PhotoError):
            await PixabayPhotoProvider(SecretStr("secret-test-key"), client).search(
                PhotoSearch(query="truck")
            )
    assert "secret-test-key" not in caplog.text and "RAW-BODY" not in caplog.text
