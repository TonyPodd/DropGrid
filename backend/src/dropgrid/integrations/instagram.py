"""Read-only Instagram readers: recent posts of a listed profile.

The official Graph API (Business Discovery) reads Business/Creator profiles on behalf
of our own professional account. Personal public profiles are read by an Apify
Instagram scraper instead. Tokens never appear in logs, errors or URLs we return.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")
# Not profiles: instagram.com/p/<post>, /reel/<id>, /explore/... and similar paths.
RESERVED = {"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct"}


class InstagramError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_username(value: str) -> str | None:
    """@name, name or an instagram.com profile link -> lowercase username."""
    text = value.strip()
    if not text:
        return None
    if "/" in text or "instagram.com" in text.casefold():
        url = text if "://" in text else "https://" + text
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
        if host not in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
            return None
        segments = [s for s in parts.path.split("/") if s]
        if not segments or segments[0].casefold() in RESERVED:
            return None
        text = segments[0]
    text = text.removeprefix("@")
    if not USERNAME.fullmatch(text) or text.startswith(".") or text.endswith("."):
        return None
    return text.casefold()


@dataclass(frozen=True)
class InstagramImage:
    media_id: str
    url: str


@dataclass
class InstagramPost:
    post_id: str
    permalink: str
    posted_at: datetime | None
    caption: str
    images: list[InstagramImage] = field(default_factory=list)


class InstagramGraphClient:
    def __init__(
        self,
        token: SecretStr | None,
        business_account_id: str | None,
        version: str,
        http: httpx.AsyncClient,
    ) -> None:
        self.token, self.account_id, self.version, self.http = (
            token,
            business_account_id,
            version,
            http,
        )

    @property
    def configured(self) -> bool:
        return bool(self.token and self.token.get_secret_value() and self.account_id)

    async def recent_posts(self, username: str, limit: int) -> list[InstagramPost]:
        if not self.configured:
            raise InstagramError("instagram_not_configured")
        assert self.token
        media = (
            f"media.limit({limit})"
            "{id,media_type,media_url,permalink,timestamp,caption,"
            "children{id,media_type,media_url}}"
        )
        try:
            response = await self.http.get(
                f"https://graph.facebook.com/{self.version}/{self.account_id}",
                params={
                    "fields": f"business_discovery.username({username}){{username,{media}}}",
                    "access_token": self.token.get_secret_value(),
                },
                timeout=20,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            # No exception context: request URLs carry the access token.
            raise InstagramError("instagram_unavailable") from None
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise InstagramError("instagram_unavailable")
        if "error" in payload:
            raise InstagramError(error_code(payload["error"]))
        discovery = payload.get("business_discovery")
        if response.status_code != 200 or not isinstance(discovery, dict):
            raise InstagramError("instagram_unavailable")
        items = (discovery.get("media") or {}).get("data") or []
        return [post for item in items if (post := parse_post(item)) is not None]


def error_code(error: object) -> str:
    """Graph errors -> fixed codes; messages are never echoed (they may quote input)."""
    code = error.get("code") if isinstance(error, dict) else None
    if code in {4, 17, 32, 613} or (isinstance(code, int) and 80000 <= code < 80100):
        return "instagram_rate_limited"
    if code in {102, 190}:
        return "instagram_token_invalid"
    if code in {10, 200}:
        return "instagram_permission_missing"
    # 100 / 110: unknown username or a personal (not Business/Creator) profile.
    if code in {100, 110}:
        return "instagram_profile_unavailable"
    return "instagram_unavailable"


def parse_post(item: object) -> InstagramPost | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    permalink = item.get("permalink")
    if not isinstance(permalink, str) or not permalink.startswith("https://www.instagram.com/"):
        return None
    timestamp = item.get("timestamp")
    posted_at = None
    if isinstance(timestamp, str):
        try:
            posted_at = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            posted_at = None
    caption = item.get("caption")
    post = InstagramPost(
        item["id"], permalink, posted_at, caption[:2000] if isinstance(caption, str) else ""
    )
    if item.get("media_type") == "IMAGE" and isinstance(item.get("media_url"), str):
        post.images.append(InstagramImage(item["id"], item["media_url"]))
    elif item.get("media_type") == "CAROUSEL_ALBUM":
        for child in (item.get("children") or {}).get("data") or []:
            if (
                isinstance(child, dict)
                and child.get("media_type") == "IMAGE"
                and isinstance(child.get("id"), str)
                and isinstance(child.get("media_url"), str)
            ):
                post.images.append(
                    InstagramImage(f"{item['id']}:{child['id']}", child["media_url"])
                )
    # Videos and reels are skipped: only photos are suggested.
    return post if post.images else None


class ApifyInstagramClient:
    """Any public profile via an Apify Instagram post scraper (no Instagram login)."""

    def __init__(self, token: SecretStr | None, actor: str, http: httpx.AsyncClient) -> None:
        self.token, self.actor, self.http = token, actor.replace("/", "~"), http

    @property
    def configured(self) -> bool:
        return bool(self.token and self.token.get_secret_value())

    async def recent_posts(self, username: str, limit: int) -> list[InstagramPost]:
        if not self.configured:
            raise InstagramError("instagram_not_configured")
        assert self.token
        try:
            response = await self.http.post(
                f"https://api.apify.com/v2/acts/{self.actor}/run-sync-get-dataset-items",
                params={"timeout": 240, "clean": "true"},
                headers={"Authorization": "Bearer " + self.token.get_secret_value()},
                json={"username": [username], "resultsLimit": limit},
                timeout=260,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise InstagramError("instagram_unavailable") from None
        if response.status_code in {401, 403}:
            raise InstagramError("instagram_token_invalid")
        if response.status_code == 429:
            raise InstagramError("instagram_rate_limited")
        if response.status_code not in {200, 201} or len(response.content) > 20 * 1024 * 1024:
            raise InstagramError("instagram_unavailable")
        try:
            items: Any = response.json()
        except ValueError:
            raise InstagramError("instagram_unavailable") from None
        if not isinstance(items, list):
            raise InstagramError("instagram_unavailable")
        posts = [post for item in items if (post := parse_scraped_post(item)) is not None]
        if not posts and any(isinstance(i, dict) and i.get("error") for i in items):
            # Unknown, private or blocked profile.
            raise InstagramError("instagram_profile_unavailable")
        return posts


def parse_scraped_post(item: object) -> InstagramPost | None:
    if not isinstance(item, dict) or item.get("error"):
        return None
    post_id = item.get("id") or item.get("shortCode")
    permalink = item.get("url")
    if not isinstance(post_id, str) or not isinstance(permalink, str):
        return None
    if not permalink.startswith("https://www.instagram.com/"):
        return None
    timestamp = item.get("timestamp")
    posted_at = None
    if isinstance(timestamp, str):
        try:
            posted_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            posted_at = None
    caption = item.get("caption")
    post = InstagramPost(
        post_id, permalink, posted_at, caption[:2000] if isinstance(caption, str) else ""
    )
    kind = item.get("type")
    if kind == "Image" and isinstance(item.get("displayUrl"), str):
        post.images.append(InstagramImage(post_id, item["displayUrl"]))
    elif kind == "Sidecar":
        children = item.get("childPosts") or []
        for index, child in enumerate(children):
            if (
                isinstance(child, dict)
                and child.get("type") == "Image"
                and isinstance(child.get("displayUrl"), str)
            ):
                child_id = child.get("id") if isinstance(child.get("id"), str) else str(index)
                post.images.append(InstagramImage(f"{post_id}:{child_id}", child["displayUrl"]))
        if not children:
            # Basic output lists carousel images without child metadata.
            for index, url in enumerate(item.get("images") or []):
                if isinstance(url, str):
                    post.images.append(InstagramImage(f"{post_id}:{index}", url))
    return post if post.images else None


class InstagramReader:
    """Official API first; profiles it cannot read (personal ones) go to the scraper."""

    def __init__(self, graph: InstagramGraphClient, scraper: ApifyInstagramClient) -> None:
        self.graph, self.scraper = graph, scraper

    @property
    def configured(self) -> bool:
        return self.graph.configured or self.scraper.configured

    async def recent_posts(self, username: str, limit: int) -> list[InstagramPost]:
        if self.graph.configured:
            try:
                return await self.graph.recent_posts(username, limit)
            except InstagramError as error:
                if error.code != "instagram_profile_unavailable" or not self.scraper.configured:
                    raise
        return await self.scraper.recent_posts(username, limit)
