"""The only adapter that understands Pixabay's official API response."""

import asyncio
import json
import logging
import time
from urllib.parse import quote, urlsplit

import httpx
from pydantic import SecretStr, ValidationError

from dropgrid.photos.domain import PhotoCandidate, PhotoError, PhotoSearch, PhotoSearchResult


class PixabayPhotoProvider:
    name = "pixabay"
    supports_sensitive_context = False
    endpoint = "https://pixabay.com/api/"

    def __init__(self, key: SecretStr | None, client: httpx.AsyncClient) -> None:
        self.key = key
        self.client = client
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.blocked_until = 0.0
        self.interval = 1.0
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.CRITICAL)

    async def search(self, search: PhotoSearch) -> PhotoSearchResult:
        if not self.key or not self.key.get_secret_value().strip():
            raise PhotoError("provider_unavailable")
        async with self.lock:
            now = time.monotonic()
            if now < self.blocked_until:
                raise PhotoError("provider_rate_limited", self.blocked_until - now)
            await asyncio.sleep(max(0, self.next_request - now))
            self.next_request = time.monotonic() + self.interval
        params = search.model_dump(exclude_none=True)
        params["q"] = params.pop("query")
        params["safesearch"] = "true"
        params["key"] = self.key.get_secret_value()
        try:
            async with asyncio.timeout(15):
                async with self.client.stream(
                    "GET", self.endpoint, params=params, follow_redirects=False
                ) as remote:
                    body = bytearray()
                    if remote.status_code == 200:
                        async for chunk in remote.aiter_bytes(chunk_size=65536):
                            if len(body) + len(chunk) > 2 * 1024 * 1024:
                                raise PhotoError("provider_invalid_response", request_made=True)
                            body.extend(chunk)
                    response = httpx.Response(
                        remote.status_code, headers=remote.headers, content=bytes(body)
                    )
        except (httpx.HTTPError, TimeoutError):
            raise PhotoError("provider_unavailable", request_made=True) from None
        limit = self._number(response, "X-RateLimit-Limit")
        remaining = self._number(response, "X-RateLimit-Remaining")
        reset = self._number(response, "X-RateLimit-Reset") or 60
        if limit:
            self.interval = max(1, 60 / limit)
        if remaining == 0 or response.status_code == 429:
            self.blocked_until = time.monotonic() + min(reset, 3600)
        if response.status_code == 429:
            raise PhotoError("provider_rate_limited", reset, request_made=True)
        if response.status_code in (401, 403):
            raise PhotoError("provider_authentication_failed", request_made=True)
        if response.status_code != 200:
            raise PhotoError("provider_unavailable", request_made=True)
        try:
            # Credential echoes cannot enter the persistent cache or media provenance.
            if (
                self.key.get_secret_value() in response.text
                or len(response.content) > 2 * 1024 * 1024
            ):
                raise ValueError
            data = response.json()
            if self.key.get_secret_value() in json.dumps(data, ensure_ascii=False):
                raise ValueError
            if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
                raise ValueError
            candidates = []
            for position, hit in enumerate(data["hits"][: search.per_page]):
                try:
                    candidates.append(self._candidate(hit, position))
                except (ValidationError, ValueError, TypeError, KeyError):
                    continue
        except (ValueError, TypeError):
            raise PhotoError("provider_invalid_response", request_made=True) from None
        return PhotoSearchResult(tuple(candidates), limit, remaining, reset)

    @staticmethod
    def _number(response: httpx.Response, header: str) -> int | None:
        try:
            number = int(response.headers[header])
            return number if 0 <= number <= 100000 else None
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _candidate(hit: dict[str, object], position: int) -> PhotoCandidate:
        source = str(hit["pageURL"])
        download = str(hit["largeImageURL"])
        for url, hosts in (
            (source, {"pixabay.com"}),
            (download, {"pixabay.com", "cdn.pixabay.com"}),
        ):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in hosts
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
            ):
                raise ValueError
        creator = str(hit.get("user", ""))[:200]
        user_id = int(str(hit.get("user_id", "0")))
        return PhotoCandidate(
            provider="pixabay",
            provider_asset_id=str(int(str(hit["id"]))),
            source_page_url=source,
            candidate_download_url=download,
            creator_name=creator,
            creator_url=f"https://pixabay.com/users/{quote(creator, safe='')}-{user_id}/"
            if creator and user_id > 0
            else None,
            width=int(str(hit["imageWidth"])),
            height=int(str(hit["imageHeight"])),
            media_type=str(hit["type"]),
            tags=tuple(
                tag.strip()[:100] for tag in str(hit.get("tags", "")).split(",") if tag.strip()
            )[:50],
            provider_position=position,
            image_size=int(str(hit["imageSize"])) if hit.get("imageSize") is not None else None,
            views=int(str(hit.get("views", 0))),
            downloads=int(str(hit.get("downloads", 0))),
            likes=int(str(hit.get("likes", 0))),
            license_code="pixabay-content-license",
            license_name="Pixabay Content License",
            license_url="https://pixabay.com/service/license-summary/",
            attribution_text=f"Photo by {creator} on Pixabay" if creator else "Photo from Pixabay",
        )
