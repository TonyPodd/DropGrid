import json

import httpx
import pytest

from dropgrid.photos.domain import PhotoError
from dropgrid.photos.pinterest_direct import PinterestDirectBackend, parse_page


def wire(id="123456789", **extra):
    return {
        "id": id,
        "type": "pin",
        "grid_title": "Honda Accord",
        "images": {
            "orig": {
                "url": f"https://i.pinimg.com/originals/{id}.jpg",
                "width": 1200,
                "height": 800,
            },
            "236x": {"url": f"https://i.pinimg.com/236x/{id}.jpg", "width": 236, "height": 157},
        },
        **extra,
    }


def envelope(rows, cursor="-end-"):
    return {
        "resource_response": {"status": "success", "data": {"results": rows}, "bookmark": cursor}
    }


def test_parser_ignores_non_image_bad_and_duplicate_rows():
    bad = wire("234567891")
    bad["images"]["orig"]["url"] = "https://evil.test/x.jpg"
    result = parse_page(
        envelope(
            [
                wire(),
                wire(),
                wire("234567890", is_video=True),
                bad,
                {"type": "interstitial"},
                wire("234567892", images={}),
            ]
        ),
        "honda accord",
    )
    assert result.raw_count == 6 and len(result.pins) == 1 and result.cursor is None
    pin = result.pins[0]
    assert pin.pin_id == "123456789" and pin.query == "honda accord" and pin.thumbnail_url
    assert pin.image_url.startswith("https://i.pinimg.com/")


async def test_anonymous_handshake_pagination_limit_and_no_cookie_persistence():
    calls = []

    def handle(request):
        calls.append(request)
        assert "authorization" not in request.headers
        if request.url.path == "/":
            assert "cookie" not in request.headers
            return httpx.Response(
                200, text="<html/>", headers={"Set-Cookie": "csrftoken=public-csrf; Path=/; Secure"}
            )
        assert request.headers.get("X-CSRFToken") == "public-csrf"
        options = json.loads(request.url.params["data"])["options"]
        assert options["query"] == "honda accord" and options["scope"] == "pins"
        if "bookmarks" not in options:
            return httpx.Response(200, json=envelope([wire()], "next-page"))
        assert options["bookmarks"] == ["next-page"]
        return httpx.Response(200, json=envelope([wire(), wire("234567890")], "next-page"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        backend = PinterestDirectBackend(client)
        result = await backend.search("honda accord", 25)
        assert len(result) == 2 and len(calls) == 3
        assert backend.stats["honda accord"] == {"raw_pins": 3, "valid_image_pins": 2, "pages": 2}
        assert not list(client.cookies.jar)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, text="blocked"),
        httpx.Response(200, json=envelope([])),
        httpx.Response(200, text="<html>login</html>"),
        httpx.Response(302, headers={"Location": "https://evil.test/"}),
        httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1)),
    ],
)
async def test_blocked_empty_invalid_or_oversize_response_safe(response):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        if request.url.path == "/":
            return httpx.Response(200, text="public")
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(PhotoError) as error:
            await PinterestDirectBackend(client).search("honda accord", 25)
        assert error.value.code == "pinterest_search_unavailable"
        assert count == 2 and not list(client.cookies.jar)


async def test_public_transport_pins_dns_and_echoes_only_anonymous_csrf():
    from dropgrid.photos.pinterest_direct import PinterestAnonymousTransport

    captured = []

    async def resolve(host):
        return ["8.8.8.8"]

    def handle(request):
        captured.append(request)
        return httpx.Response(200, text="public")

    transport = PinterestAnonymousTransport()
    transport.resolver = resolve
    transport.pools["www.pinterest.com"] = httpx.MockTransport(handle)
    request = httpx.Request(
        "GET",
        "https://www.pinterest.com/",
        headers={"Cookie": "session=do-not-send; csrftoken=public-csrf"},
    )
    await transport.handle_async_request(request)
    assert captured[0].url.host == "8.8.8.8"
    assert captured[0].headers["Host"] == "www.pinterest.com"
    assert captured[0].headers["Cookie"] == "csrftoken=public-csrf"
    assert captured[0].extensions["sni_hostname"] == "www.pinterest.com"
    with pytest.raises(PhotoError):
        await transport.handle_async_request(
            httpx.Request(
                "GET", "https://www.pinterest.com/", headers={"Authorization": "Bearer never-send"}
            )
        )
    await transport.aclose()
