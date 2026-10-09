"""Anonymous, bounded public resource search. No browser automation or evasion.

Protocol reference: https://github.com/tamnd/pinterest-cli (Apache-2.0).
This Python implementation is independently written; no CLI is invoked.
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import quote_plus, urljoin, urlsplit

import httpx

from dropgrid.photos.domain import PhotoError
from dropgrid.photos.download import PinnedPhotoTransport, public_addresses, validate_url
from dropgrid.photos.pinterest import PinterestPolicy, pin_candidate

BASE = "https://www.pinterest.com"
MAX_BODY = 4 * 1024 * 1024
MAX_PAGES = 2
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublicPinterestPolicy(PinterestPolicy):
    trusted_hosts: frozenset[str] = frozenset({"www.pinterest.com", "pinterest.com"})


class PinterestAnonymousTransport(PinnedPhotoTransport):
    """DNS-pinned HTTP; ephemeral cookies learned from the anonymous client only."""

    def __init__(self) -> None:
        super().__init__(PublicPinterestPolicy())
        self.anonymous_cookie_pairs: frozenset[str] = frozenset()

    def bind_anonymous_cookies(self, cookies: httpx.Cookies) -> None:
        # The backend clears its dedicated jar before homepage warmup. No imported
        # account cookies can be authorized here; nothing is persisted across searches.
        self.anonymous_cookie_pairs = frozenset(
            f"{cookie.name}={cookie.value}"
            for cookie in cookies.jar
            if cookie.domain.lstrip(".") in self.policy.trusted_hosts
            and len(cookie.name) <= 100
            and isinstance(cookie.value, str)
            and len(cookie.value) <= 4096
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers:
            raise PhotoError("pinterest_search_unavailable")
        cookies = request.headers.pop("cookie", "")
        permitted = [
            part.strip()
            for part in cookies.split(";")
            if part.strip() in self.anonymous_cookie_pairs
        ]
        if permitted and sum(map(len, permitted)) <= 16384:
            request.headers["cookie"] = "; ".join(permitted)
        host = validate_url(str(request.url), self.policy)
        addresses = await public_addresses(host, self.resolver)
        request.headers["host"] = host
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return await self.pools[host].handle_async_request(pinned)


@dataclass(frozen=True)
class PinterestPinCandidate:
    pin_id: str
    pin_url: str
    image_url: str
    width: int
    height: int
    title: str = ""
    description: str = ""
    alt_text: str = ""
    query: str = ""
    thumbnail_url: str | None = None

    def record(self) -> dict[str, object]:
        return {
            "id": self.pin_id,
            "url": self.pin_url,
            "imageUrl": self.image_url,
            "width": self.width,
            "height": self.height,
            "title": self.title,
            "description": self.description,
            "alt": self.alt_text,
            "query": self.query,
            "thumbnailUrl": self.thumbnail_url,
        }


@dataclass
class PinterestPage:
    pins: list[PinterestPinCandidate] = field(default_factory=list)
    cursor: str | None = None
    raw_count: int = 0


def parse_page(payload: object, query: str) -> PinterestPage:
    if not isinstance(payload, dict):
        raise PhotoError("pinterest_search_unavailable")
    envelope = payload.get("resource_response")
    if (
        not isinstance(envelope, dict)
        or envelope.get("error")
        or envelope.get("status") in {"failure", "error"}
    ):
        raise PhotoError("pinterest_search_unavailable")
    data = envelope.get("data")
    if isinstance(data, dict):
        results = data.get("results")
        data = results if isinstance(results, list) and results else data.get("data", results)
    if not isinstance(data, list):
        raise PhotoError("pinterest_search_unavailable")
    bookmark = envelope.get("bookmark")
    cursor = (
        bookmark
        if isinstance(bookmark, str)
        and 0 < len(bookmark) <= 4096
        and bookmark != "-end-"
        and not bookmark.startswith("Y2JOb25l")
        else None
    )
    result = PinterestPage(cursor=cursor, raw_count=min(len(data), 100))
    seen = set()
    for raw in data[:100]:
        if (
            not isinstance(raw, dict)
            or raw.get("type") not in (None, "pin")
            or raw.get("is_video")
            or raw.get("videos")
            or raw.get("story_pin_data")
        ):
            continue
        identity, images = raw.get("id"), raw.get("images")
        if not isinstance(identity, (str, int)) or not isinstance(images, dict):
            continue
        choices = [
            value
            for value in images.values()
            if isinstance(value, dict)
            and isinstance(value.get("url"), str)
            and type(value.get("width")) is int
            and type(value.get("height")) is int
        ]
        choices.sort(key=lambda image: -(image["width"] * image["height"]))
        for image in choices:
            row = {
                "id": str(identity),
                "url": f"{BASE}/pin/{identity}/",
                "imageUrl": image["url"],
                "width": image["width"],
                "height": image["height"],
                "title": raw.get("grid_title") or raw.get("title") or "",
                "description": raw.get("description") or "",
                "alt": raw.get("auto_alt_text") or "",
            }
            try:
                photo = pin_candidate(row, 0)
            except (PhotoError, ValueError):
                continue
            if photo.provider_asset_id not in seen:
                thumbnail = images.get("236x", images.get("474x"))
                thumb = thumbnail.get("url") if isinstance(thumbnail, dict) else None
                if (
                    not isinstance(thumb, str)
                    or urlsplit(thumb).scheme != "https"
                    or urlsplit(thumb).hostname != "i.pinimg.com"
                ):
                    thumb = None
                result.pins.append(
                    PinterestPinCandidate(
                        photo.provider_asset_id,
                        photo.source_page_url,
                        photo.candidate_download_url,
                        photo.width,
                        photo.height,
                        photo.title,
                        photo.description,
                        photo.alt_text,
                        query,
                        thumb,
                    )
                )
                seen.add(photo.provider_asset_id)
            break
    return result


@dataclass
class PinterestDiagnostics:
    http_status: int | None = None
    response_content_type: str | None = None
    resource_status: str | None = None
    has_data: bool | None = None
    raw_result_count: int | None = None

    def response(self, response: httpx.Response) -> None:
        self.http_status = response.status_code
        media_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
        self.response_content_type = (
            media_type
            if media_type
            in {"application/json", "text/html", "text/plain", "application/xhtml+xml"}
            else "other"
        )

    def payload(self, payload: object) -> None:
        envelope = payload.get("resource_response") if isinstance(payload, dict) else None
        if not isinstance(envelope, dict):
            self.has_data, self.raw_result_count = False, 0
            self.resource_status = "unknown"
            return
        status = envelope.get("status")
        self.resource_status = status if status in ("success", "failure", "error") else "unknown"
        data = envelope.get("data")
        self.has_data = data is not None
        if isinstance(data, dict):
            results = data.get("results")
            data = results if isinstance(results, list) and results else data.get("data", results)
        self.raw_result_count = len(data) if isinstance(data, list) else 0


class PinterestDirectBackend:
    def __init__(
        self, client: httpx.AsyncClient, transport: PinterestAnonymousTransport | None = None
    ) -> None:
        self.client = client
        self.transport = transport
        self.diagnostics: dict[str, PinterestDiagnostics] = {}
        self._diagnostic = PinterestDiagnostics()
        self.lock = asyncio.Lock()
        self.stats: dict[str, dict[str, int]] = {}

    def _sync_cookies(self) -> None:
        if self.transport:
            self.transport.bind_anonymous_cookies(self.client.cookies)

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        url = BASE + path
        for _ in range(3):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in PublicPinterestPolicy().trusted_hosts
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
            ):
                raise PhotoError("pinterest_search_unavailable")
            async with self.client.stream("GET", url, params=params, headers=headers) as response:
                self._diagnostic.response(response)
                self._sync_cookies()
                if response.is_redirect:
                    url = urljoin(str(response.url), response.headers.get("location", ""))
                    params = None
                    continue
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise PhotoError("pinterest_search_unavailable")
                return bytes(body)
        raise PhotoError("pinterest_search_unavailable")

    async def search_page(
        self, query: str, limit: int = 25, cursor: str | None = None
    ) -> PinterestPage:
        if (
            not query
            or len(query) > 100
            or not 1 <= limit <= 30
            or cursor is not None
            and len(cursor) > 4096
        ):
            raise PhotoError("pinterest_search_unavailable")
        source = "/search/pins/?q=" + quote_plus(query) + "&rs=typed"
        options: dict[str, object] = {
            "query": query,
            "scope": "pins",
            "rs": "typed",
            "page_size": 25,
        }
        if cursor:
            options["bookmarks"] = [cursor]
        return await self._resource_page("BaseSearchResource", source, options, query)

    async def _resource_page(
        self, resource: str, source: str, options: dict[str, object], query: str
    ) -> PinterestPage:
        headers = {
            "Accept": "application/json, text/javascript, */*, q=0.01",
            "X-Pinterest-PWS-Handler": "www/[username].js",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE + source,
        }
        csrf = next(
            (
                cookie.value
                for cookie in self.client.cookies.jar
                if cookie.name == "csrftoken"
                and cookie.domain.lstrip(".") in {"pinterest.com", "www.pinterest.com"}
            ),
            None,
        )
        if csrf:
            headers["X-CSRFToken"] = csrf
        body = await self._get(
            f"/resource/{resource}/get/",
            params={
                "source_url": source,
                "_": str(time.time_ns() // 1_000_000),
                "data": json.dumps({"options": options, "context": {}}, separators=(",", ":")),
            },
            headers=headers,
        )
        payload = json.loads(body)
        self._diagnostic.payload(payload)
        return parse_page(payload, query)

    async def related(self, pin_id: str, limit: int = 20) -> list[dict[str, object]]:
        """Development experiment capability; never called by production discovery.

        One anonymous resource page, no pagination. A caller may choose at most
        two already-ranked seeds; this adapter caps each seed at 20 results.
        """
        if (
            not pin_id.isascii()
            or not pin_id.isdigit()
            or not 8 <= len(pin_id) <= 30
            or not 1 <= limit <= 20
        ):
            raise PhotoError("pinterest_candidate_invalid")
        async with self.lock:
            self.client.cookies.clear()
            self._sync_cookies()
            self._diagnostic = PinterestDiagnostics()
            try:
                async with asyncio.timeout(20):
                    await self._get(
                        "/",
                        headers={
                            "Accept": (
                                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                            )
                        },
                    )
                    page = await self._resource_page(
                        "RelatedPinFeedResource",
                        f"/pin/{pin_id}/",
                        {"pin_id": pin_id, "page_size": 25},
                        f"related:{pin_id}",
                    )
                return [p.record() for p in page.pins if p.pin_id != pin_id][:limit]
            except (httpx.HTTPError, PhotoError, ValueError, TypeError, TimeoutError):
                raise PhotoError("pinterest_search_unavailable") from None
            finally:
                self.client.cookies.clear()
                self._sync_cookies()

    async def search(self, query: str, limit: int) -> list[dict[str, object]]:
        async with self.lock:
            self.client.cookies.clear()
            self._sync_cookies()
            self._diagnostic = PinterestDiagnostics()
            pins: dict[str, PinterestPinCandidate] = {}
            raw_count, pages = 0, 0
            try:
                async with asyncio.timeout(35):
                    await self._get(
                        "/",
                        headers={
                            "Accept": (
                                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                            )
                        },
                    )
                    cursor, cursors = None, set()
                    for _ in range(MAX_PAGES):
                        page = await self.search_page(query, min(limit, 30), cursor)
                        raw_count += page.raw_count
                        pages += 1
                        for pin in page.pins:
                            pins.setdefault(pin.pin_id, pin)
                        if (
                            len(pins) >= limit
                            or not page.cursor
                            or page.cursor in cursors
                            or page.raw_count == 0
                        ):
                            break
                        cursor = page.cursor
                        cursors.add(cursor)
                if not pins:
                    raise PhotoError("pinterest_search_unavailable")
                return [pin.record() for pin in list(pins.values())[: min(limit, 30)]]
            except (httpx.HTTPError, PhotoError, ValueError, TypeError, TimeoutError):
                raise PhotoError("pinterest_search_unavailable") from None
            finally:
                self.stats[query] = {
                    "raw_pins": raw_count,
                    "valid_image_pins": len(pins),
                    "pages": pages,
                }
                self.client.cookies.clear()
                self._sync_cookies()
                self.diagnostics[query] = self._diagnostic
                logger.debug("pinterest.public_search", extra={"counts": asdict(self._diagnostic)})
