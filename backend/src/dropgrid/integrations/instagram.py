"""Read-only Instagram readers: recent posts of a listed profile.

By default public profiles are opened directly, like Pinterest: no token, no login,
an honest client name; when Instagram asks to log in or slows us down we stop and try
later, never work around it. Optional readers: the official Graph API (Business
Discovery; Business/Creator profiles on behalf of our professional account) and an
Apify scraper. Tokens never appear in logs, errors or URLs we return.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
        """Newest first, page by page (50 per page) until `limit` posts or the end."""
        posts: list[InstagramPost] = []
        cursor = None
        while len(posts) < limit:
            page, cursor = await self.posts_page(username, cursor, limit - len(posts))
            posts.extend(page)
            if cursor is None:
                break
        return posts[:limit]

    async def posts_page(
        self, username: str, cursor: str | None, limit: int
    ) -> tuple[list[InstagramPost], str | None]:
        """One page of up to 50 posts after `cursor`, and the cursor of the next one."""
        if not self.configured:
            raise InstagramError("instagram_not_configured")
        after = f".after({cursor})" if cursor and CURSOR.fullmatch(cursor) else ""
        fields = (
            f"business_discovery.username({username}){{username,"
            f"media{after}.limit({max(1, min(50, limit))})"
            "{id,media_type,media_url,permalink,timestamp,caption,"
            "children{id,media_type,media_url}}}"
        )
        media = await self._media(fields)
        items = media.get("data") or []
        paging = media.get("paging") or {}
        following = (paging.get("cursors") or {}).get("after")
        if (
            not items
            or not paging.get("next")
            or not isinstance(following, str)
            or not CURSOR.fullmatch(following)
        ):
            following = None
        return [post for item in items if (post := parse_post(item)) is not None], following

    async def _media(self, fields: str) -> dict[str, Any]:
        assert self.token
        try:
            response = await self.http.get(
                f"https://graph.facebook.com/{self.version}/{self.account_id}",
                params={"fields": fields, "access_token": self.token.get_secret_value()},
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
        media = discovery.get("media") or {}
        return media if isinstance(media, dict) else {}


# Paging cursors are opaque base64-like tokens; anything else is not put into a query.
CURSOR = re.compile(r"[A-Za-z0-9_=-]{1,1024}")


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


class SinglePage:
    """Readers without cursors return everything they can in one page."""

    async def recent_posts(self, username: str, limit: int) -> list[InstagramPost]:
        raise NotImplementedError

    async def posts_page(
        self, username: str, cursor: str | None, limit: int
    ) -> tuple[list[InstagramPost], str | None]:
        return await self.recent_posts(username, limit), None


class ApifyInstagramClient(SinglePage):
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


class InstagramWebClient(SinglePage):
    """Public profile pages without login: the newest 12 posts of an open profile."""

    def __init__(self, http: httpx.AsyncClient, enabled: bool = True) -> None:
        self.http, self.enabled = http, enabled

    @property
    def configured(self) -> bool:
        return self.enabled

    async def recent_posts(self, username: str, limit: int) -> list[InstagramPost]:
        if not self.enabled:
            raise InstagramError("instagram_not_configured")
        try:
            response = await self.http.get(
                "https://www.instagram.com/api/v1/users/web_profile_info/",
                params={"username": username},
                # The public web app's id; the endpoint answers only with it.
                headers={"X-IG-App-ID": WEB_APP_ID, "Accept": "application/json"},
                timeout=20,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise InstagramError("instagram_unavailable") from None
        if response.status_code == 404:
            raise InstagramError("instagram_profile_unavailable")
        if response.status_code == 429:
            raise InstagramError("instagram_rate_limited")
        if response.status_code in {301, 302, 303, 307, 308, 401, 403}:
            # Login wall or this address is not served anonymously: stop, try later.
            raise InstagramError("instagram_login_required")
        if response.status_code != 200 or len(response.content) > 10 * 1024 * 1024:
            raise InstagramError("instagram_unavailable")
        try:
            payload: Any = response.json()
        except ValueError:
            raise InstagramError("instagram_login_required") from None
        user = (payload.get("data") or {}).get("user") if isinstance(payload, dict) else None
        if not isinstance(user, dict):
            if isinstance(payload, dict) and payload.get("require_login"):
                raise InstagramError("instagram_login_required")
            raise InstagramError("instagram_profile_unavailable")
        if user.get("is_private"):
            raise InstagramError("instagram_profile_private")
        edges = (user.get("edge_owner_to_timeline_media") or {}).get("edges") or []
        posts = []
        for edge in edges[:limit]:
            node = edge.get("node") if isinstance(edge, dict) else None
            if (post := parse_web_node(node)) is not None:
                posts.append(post)
        return posts


WEB_APP_ID = "936619743392459"


def parse_web_node(node: object) -> InstagramPost | None:
    if not isinstance(node, dict) or not isinstance(node.get("shortcode"), str):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,64}", node["shortcode"]):
        return None
    raw_id = node.get("id")
    post_id: str = raw_id if isinstance(raw_id, str) else node["shortcode"]
    taken = node.get("taken_at_timestamp")
    posted_at = (
        datetime.fromtimestamp(taken, tz=UTC) if isinstance(taken, int) and taken > 0 else None
    )
    captions = ((node.get("edge_media_to_caption") or {}).get("edges")) or []
    caption = ""
    if captions and isinstance(captions[0], dict):
        text = (captions[0].get("node") or {}).get("text")
        caption = text[:2000] if isinstance(text, str) else ""
    post = InstagramPost(
        post_id, f"https://www.instagram.com/p/{node['shortcode']}/", posted_at, caption
    )
    children = ((node.get("edge_sidecar_to_children") or {}).get("edges")) or []
    if children:
        for index, child in enumerate(children):
            item = child.get("node") if isinstance(child, dict) else None
            if (
                isinstance(item, dict)
                and not item.get("is_video")
                and isinstance(item.get("display_url"), str)
            ):
                raw_child = item.get("id")
                child_id = raw_child if isinstance(raw_child, str) else str(index)
                post.images.append(InstagramImage(f"{post_id}:{child_id}", item["display_url"]))
    elif not node.get("is_video") and isinstance(node.get("display_url"), str):
        post.images.append(InstagramImage(post_id, node["display_url"]))
    return post if post.images else None


class InstagramReader:
    """Official API for Business/Creator profiles when connected; everything else is
    opened directly (or by the Apify scraper when its token is set)."""

    def __init__(
        self,
        graph: InstagramGraphClient,
        scraper: ApifyInstagramClient,
        web: InstagramWebClient | None = None,
    ) -> None:
        self.graph, self.scraper = graph, scraper
        self.web = web or InstagramWebClient(httpx.AsyncClient(), enabled=False)

    @property
    def configured(self) -> bool:
        return self.graph.configured or self.scraper.configured or self.web.configured

    async def recent_posts(self, username: str, limit: int) -> list[InstagramPost]:
        fallback = self.scraper if self.scraper.configured else self.web
        if self.graph.configured:
            try:
                return await self.graph.recent_posts(username, limit)
            except InstagramError as error:
                if error.code != "instagram_profile_unavailable" or not fallback.configured:
                    raise
        return await fallback.recent_posts(username, limit)

    async def posts_page(
        self, username: str, cursor: str | None, limit: int
    ) -> tuple[list[InstagramPost], str | None]:
        fallback = self.scraper if self.scraper.configured else self.web
        if self.graph.configured:
            try:
                return await self.graph.posts_page(username, cursor, limit)
            except InstagramError as error:
                if error.code != "instagram_profile_unavailable" or not fallback.configured:
                    raise
        return await fallback.posts_page(username, None, limit)
