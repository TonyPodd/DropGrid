"""Experimental public Pin retrieval behind a replaceable hosted search backend."""

import asyncio
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from dropgrid.photos.domain import (
    PhotoCandidate,
    PhotoError,
    PhotoPolicy,
    PhotoSearch,
    PhotoSearchResult,
)
from dropgrid.photos.download import validate_url


@dataclass(frozen=True)
class PinterestPolicy(PhotoPolicy):
    min_short_side: int = 256
    max_input_bytes: int = 8 * 1024 * 1024
    max_pixels: int = 20_000_000
    max_redirects: int = 2
    timeout_seconds: float = 10
    trusted_hosts: frozenset[str] = frozenset({"i.pinimg.com"})


class PinterestSearchBackend(Protocol):
    async def search(self, query: str, limit: int) -> list[dict[str, object]]: ...


class ApifyPinterestBackend:
    """Configurable actor with the documented searchQueries/maxPins contract.

    No automatic retries of actor runs. Bearer credentials stay out of URLs.
    Actor must return Pin metadata, never cookies or login/session material.
    """

    def __init__(self, token: SecretStr, actor: str, client: httpx.AsyncClient) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:[~/][A-Za-z0-9_-]+)?", actor):
            raise PhotoError("pinterest_backend_unavailable")
        self.token, self.actor, self.client = token, actor.replace("/", "~"), client

    async def search(self, query: str, limit: int) -> list[dict[str, object]]:
        headers = {"Authorization": "Bearer " + self.token.get_secret_value()}
        try:
            async with asyncio.timeout(50):
                response = await self.client.post(
                    f"https://api.apify.com/v2/actors/{self.actor}/runs",
                    headers=headers,
                    params={"timeout": 45, "waitForFinish": 45, "maxItems": min(limit, 25)},
                    json={"searchQueries": [query], "maxPins": min(limit, 25)},
                    timeout=48,
                )
                response.raise_for_status()
                run = response.json().get("data", {})
                dataset = run.get("defaultDatasetId")
                if (
                    run.get("status") != "SUCCEEDED"
                    or not isinstance(dataset, str)
                    or not re.fullmatch(r"[A-Za-z0-9]+", dataset)
                ):
                    raise PhotoError("pinterest_search_unavailable")
                response = await self.client.get(
                    f"https://api.apify.com/v2/datasets/{dataset}/items",
                    headers=headers,
                    params={"clean": "true", "limit": min(limit, 25)},
                    timeout=5,
                )
                response.raise_for_status()
                if len(response.content) > 2 * 1024 * 1024:
                    raise PhotoError("pinterest_search_unavailable")
                items = response.json()
                if not isinstance(items, list):
                    raise PhotoError("pinterest_search_unavailable")
                return [row for row in items[: min(limit, 25)] if isinstance(row, dict)]
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, TimeoutError):
            raise PhotoError("pinterest_search_unavailable") from None


def pin_candidate(row: dict[str, object], position: int) -> PhotoCandidate:
    identity = str(row.get("id", row.get("pinId", "")))
    page, image = row.get("url", row.get("pinUrl")), row.get("imageUrl")
    if (
        not re.fullmatch(r"[0-9]{5,30}", identity)
        or not isinstance(page, str)
        or not isinstance(image, str)
    ):
        raise PhotoError("pinterest_candidate_invalid")
    parsed = urlsplit(page)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.pinterest.com", "pinterest.com"}
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.path.rstrip("/") != "/pin/" + identity
        or parsed.query
        or parsed.fragment
    ):
        raise PhotoError("pinterest_candidate_invalid")
    validate_url(image, PinterestPolicy())
    width, height = row.get("width"), row.get("height")
    if (
        type(width) is not int
        or type(height) is not int
        or width * height > PinterestPolicy().max_pixels
        or not PinterestPolicy().dimensions_allowed(width, height)
    ):
        raise PhotoError("pinterest_candidate_invalid")

    def text(name: str) -> str:
        value = row.get(name)
        return value[:2000] if isinstance(value, str) else ""

    title, description, alt = text("title"), text("description"), text("alt")
    return PhotoCandidate(
        provider="pinterest",
        provider_asset_id=identity,
        source_page_url=page,
        candidate_download_url=image,
        width=width,
        height=height,
        tags=tuple(
            re.findall(r"[a-z0-9]+", (title + " " + description + " " + alt).casefold())[:100]
        ),
        title=title,
        description=description,
        alt_text=alt,
        provider_position=position,
        license_code="unverified-public-pin",
        license_name="Publication rights unverified",
        license_url="https://policy.pinterest.com/en/copyright",
        publication_eligible=False,
    )


class PinterestPhotoProvider:
    name = "pinterest"
    supports_sensitive_context = False

    def __init__(self, backend: PinterestSearchBackend | None) -> None:
        self.backend = backend

    async def search(self, search: PhotoSearch) -> PhotoSearchResult:
        if self.backend is None:
            raise PhotoError("pinterest_backend_unavailable")
        rows = await self.backend.search(search.query, 25)
        candidates: dict[str, PhotoCandidate] = {}
        for position, row in enumerate(rows[:25]):
            try:
                photo = pin_candidate(row, position)
                candidates.setdefault(photo.provider_asset_id, photo)
            except (PhotoError, ValueError):
                continue
        return PhotoSearchResult(tuple(candidates.values()))
