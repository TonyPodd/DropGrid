from dataclasses import replace

import httpx
import pytest
from photo_fixtures import image_bytes

from dropgrid.photos.domain import PhotoError, PhotoPolicy
from dropgrid.photos.download import PhotoDownloader, PinnedPhotoTransport


async def public(host):
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.pixabay.com/a.jpg",
        "https://localhost/a",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://10.0.0.1/a",
        "https://169.254.169.254/a",
        "https://evil.example/a",
        "https://user:pass@cdn.pixabay.com/a",
        "https://cdn.pixabay.com:8765/a",
    ],
)
async def test_rejected_urls(url):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("Network must not be called"))
    ) as client:
        with pytest.raises(PhotoError):
            await PhotoDownloader(client, PhotoPolicy(), public).download(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "::1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "198.18.0.1",
        "0.0.0.0",
        "::",
    ],
)
async def test_dns_private_rejected(ip):
    async def resolve(host):
        return [ip]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("Network must not be called"))
    ) as client:
        with pytest.raises(PhotoError):
            await PhotoDownloader(client, PhotoPolicy(), resolve).download(
                "https://cdn.pixabay.com/a.jpg"
            )


async def test_redirect_to_private():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(302, headers={"Location": "https://169.254.169.254/a"})
        )
    ) as client:
        with pytest.raises(PhotoError):
            await PhotoDownloader(client, PhotoPolicy(), public).download(
                "https://cdn.pixabay.com/a.jpg"
            )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"x" * 1001, headers={"content-type": "image/jpeg"}),
        httpx.Response(
            200, content=b"x", headers={"content-type": "image/jpeg", "content-length": "999999999"}
        ),
        httpx.Response(200, content=b"html", headers={"content-type": "text/html"}),
        httpx.Response(500, text="RAW ERROR"),
    ],
)
async def test_download_bounds(response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response)) as client:
        with pytest.raises(PhotoError):
            await PhotoDownloader(
                client, replace(PhotoPolicy(), max_input_bytes=1000), public
            ).download("https://cdn.pixabay.com/a.jpg")


async def test_pinning_keeps_sni_host_and_rejects_dns_rebinding():
    calls = []

    async def resolver(host):
        return ["93.184.216.34"]

    transport = PinnedPhotoTransport(PhotoPolicy(), resolver)

    async def handle(request):
        calls.append(request)
        return httpx.Response(200, content=image_bytes(), headers={"content-type": "image/jpeg"})

    await transport.pools["cdn.pixabay.com"].aclose()
    transport.pools["cdn.pixabay.com"] = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await PhotoDownloader(client, PhotoPolicy(), resolver).download(
            "https://cdn.pixabay.com/a.jpg"
        )
        assert data
    assert calls[0].url.host == "93.184.216.34"
    assert calls[0].headers["host"] == "cdn.pixabay.com"
    assert calls[0].extensions["sni_hostname"] == "cdn.pixabay.com"
    sequence = iter([["93.184.216.34"], ["127.0.0.1"]])

    async def rebind(host):
        return next(sequence)

    transport = PinnedPhotoTransport(PhotoPolicy(), rebind)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PhotoError):
            await PhotoDownloader(client, PhotoPolicy(), rebind).download(
                "https://cdn.pixabay.com/a.jpg"
            )


async def test_download_concurrency_and_individual_timeout_are_bounded():
    import asyncio
    from dataclasses import replace

    current = peak = 0

    async def handler(r):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        try:
            await asyncio.sleep(0.02 if r.url.path != "/slow.jpg" else 0.2)
            return httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        finally:
            current -= 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        downloader = PhotoDownloader(
            http, replace(PhotoPolicy(), timeout_seconds=0.1), public, slots=asyncio.Semaphore(2)
        )
        results = await asyncio.gather(
            *(downloader.download(f"https://cdn.pixabay.com/{i}.jpg") for i in range(4))
        )
        assert all(results) and peak == 2
        with pytest.raises(PhotoError, match="download_failed"):
            await downloader.download("https://cdn.pixabay.com/slow.jpg")
        assert await downloader.download("https://cdn.pixabay.com/again.jpg")
