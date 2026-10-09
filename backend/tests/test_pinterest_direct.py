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


async def test_anonymous_handshake_pagination_limit_and_no_cookie_persistence(monkeypatch):
    monkeypatch.setattr(
        "dropgrid.photos.pinterest_direct.time.time_ns", lambda: 1791500000123000000
    )
    calls = []

    def handle(request):
        calls.append(request)
        assert "authorization" not in request.headers
        if request.url.path == "/":
            assert "cookie" not in request.headers
            assert (
                request.headers["Accept"]
                == "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            )
            return httpx.Response(
                200, text="<html/>", headers={"Set-Cookie": "csrftoken=public-csrf; Path=/; Secure"}
            )
        assert request.headers.get("X-CSRFToken") == "public-csrf"
        assert request.url.params["_"] == "1791500000123"
        assert request.url.params["source_url"] == "/search/pins/?q=honda+accord&rs=typed"
        assert request.headers["X-Pinterest-PWS-Handler"] == "www/[username].js"
        assert request.headers["Accept"] == "application/json, text/javascript, */*, q=0.01"
        options = json.loads(request.url.params["data"])["options"]
        assert options["page_size"] == 25
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


async def test_public_transport_pins_dns_and_requires_anonymous_cookie_binding():
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
    jar = httpx.Cookies()
    jar.set("csrftoken", "public-csrf", domain="www.pinterest.com", path="/")
    transport.bind_anonymous_cookies(jar)
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


def test_search_envelope_fallback_matches_upstream_decoder():
    payload = envelope([])
    payload["resource_response"]["data"]["data"] = [wire()]
    assert len(parse_page(payload, "honda accord").pins) == 1


async def test_warmup_cookies_ephemeral_trusted_and_account_cookies_never_imported():
    from dropgrid.photos.pinterest_direct import PinterestAnonymousTransport

    transport = PinterestAnonymousTransport()

    async def resolve(host):
        return ["8.8.8.8"]

    transport.resolver = resolve
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/":
            assert "cookie" not in request.headers
            return httpx.Response(
                200,
                headers=[
                    ("Set-Cookie", "csrftoken=anon-csrf; Domain=.pinterest.com; Path=/; Secure"),
                    (
                        "Set-Cookie",
                        "_pinterest_sess=anonymous-warmup; Domain=.pinterest.com; Path=/; Secure",
                    ),
                ],
                text="public",
            )
        assert "_pinterest_sess=anonymous-warmup" in request.headers["cookie"]
        assert "csrftoken=anon-csrf" in request.headers["cookie"]
        assert "account-secret" not in request.headers["cookie"]
        assert request.headers["X-CSRFToken"] == "anon-csrf"
        return httpx.Response(200, json=envelope([wire()]))

    transport.pools["www.pinterest.com"] = httpx.MockTransport(handle)
    async with httpx.AsyncClient(
        transport=transport, cookies={"session": "account-secret"}
    ) as client:
        backend = PinterestDirectBackend(client, transport)
        assert len(await backend.search("honda accord", 10)) == 1
        assert len(calls) == 2
        assert not list(client.cookies.jar) and not transport.anonymous_cookie_pairs


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(403, headers={"content-type": "text/html"}, text="private body"),
            (403, "text/html", None, None, None),
        ),
        (
            httpx.Response(200, headers={"content-type": "text/html"}, text="login private body"),
            (200, "text/html", None, None, None),
        ),
        (httpx.Response(200, json=envelope([])), (200, "application/json", "success", True, 0)),
        (
            httpx.Response(200, json={"resource_response": {"status": "failure", "data": None}}),
            (200, "application/json", "failure", False, 0),
        ),
        (
            httpx.Response(
                200, json=envelope([{"type": "interstitial", "secret": "private body"}])
            ),
            (200, "application/json", "success", True, 1),
        ),
    ],
)
async def test_safe_diagnostics_distinguish_status_html_empty_and_parser_mismatch(
    response, expected, caplog
):
    import logging
    from dataclasses import asdict

    def handle(request):
        return httpx.Response(200, text="public") if request.url.path == "/" else response

    caplog.set_level(logging.DEBUG, logger="dropgrid.photos.pinterest_direct")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        backend = PinterestDirectBackend(client)
        with pytest.raises(PhotoError, match="pinterest_search_unavailable"):
            await backend.search("honda accord", 10)
        diagnostic = asdict(backend.diagnostics["honda accord"])
        assert tuple(diagnostic.values()) == expected
        from dropgrid.logging import JsonFormatter

        logged = next(
            record for record in caplog.records if record.name == "dropgrid.photos.pinterest_direct"
        )
        assert json.loads(JsonFormatter().format(logged))["counts"] == diagnostic
        assert "private body" not in str(diagnostic) and "private body" not in caplog.text
        assert not list(client.cookies.jar)
