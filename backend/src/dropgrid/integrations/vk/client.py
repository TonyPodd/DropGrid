import asyncio
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Self
from uuid import UUID

import httpx
from pydantic import SecretStr, TypeAdapter, ValidationError

from dropgrid.config import Settings
from dropgrid.domain.grid_parser import normalize_vk_community_reference
from dropgrid.integrations.vk.errors import (
    VKAPIError,
    VKAuthenticationError,
    VKCaptchaRequiredError,
    VKCommunityUnavailableError,
    VKError,
    VKInputError,
    VKPermissionError,
    VKProtocolError,
    VKRateLimitError,
    VKTransportError,
    VKWriteDisabledError,
    api_error,
)
from dropgrid.integrations.vk.limiter import LocalRateLimiter, Sleeper, VKRateLimiter
from dropgrid.integrations.vk.models import (
    CommunityResolution,
    ResolutionStatus,
    VKCommunity,
    VKNotifications,
    VKUser,
    WallPostReceipt,
    WallPosts,
    WallPostsById,
)
from dropgrid.photos.timings import timed

_READ_METHODS = frozenset(
    {"users.get", "groups.getById", "wall.get", "wall.getById", "notifications.get"}
)
_WRITE_METHODS = frozenset({"wall.post", "photos.getWallUploadServer", "photos.saveWallPhoto"})
logger = logging.getLogger(__name__)


def parse_response[T](adapter: TypeAdapter[T], data: object, method: str) -> T:
    error: VKProtocolError | None = None
    try:
        return adapter.validate_python(data)
    except ValidationError:
        error = VKProtocolError(method, "Unexpected VK response structure")
    raise error


class VKClient:
    """Pooled official API client. `call` returns the checked response envelope.

    Injection clients remain owned by the caller. Unknown methods fail closed.
    Read-only calls may retry; writes are single-attempt to avoid duplicate effects.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: VKRateLimiter | None = None,
        sleeper: Sleeper = asyncio.sleep,
        backoff: Callable[[int], float] = lambda retry: 0.25 * 2 ** (retry - 1),
    ) -> None:
        if http_client is not None and transport is not None:
            raise ValueError("Provide client or transport, not both")
        self.settings = settings
        self.read_slots = asyncio.Semaphore(settings.vk_read_concurrency)
        self.limiter = limiter or LocalRateLimiter(settings.vk_min_interval_seconds)
        self.sleeper = sleeper
        self.backoff = backoff
        self._owns_http = http_client is None
        self.http = (
            http_client
            if http_client is not None
            else httpx.AsyncClient(
                transport=transport,
                timeout=settings.vk_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        )
        # Upload URLs can contain capabilities; third-party URL logging is unsafe.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.CRITICAL)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    def require_write(self, method: str = "wall.post") -> None:
        from dropgrid.integrations.vk.read_only import assert_write_allowed

        assert_write_allowed(method)
        if not self.settings.vk_write_enabled:
            raise VKWriteDisabledError(method, "VK writes are disabled")

    def require_diagnostic_target(self, community_id: int) -> None:
        self.require_write()
        if (
            type(community_id) is not int
            or community_id not in self.settings.vk_test_allowed_community_ids
        ):
            raise VKWriteDisabledError("wall.post", "Community is not explicitly allowlisted")

    async def call(
        self,
        method: str,
        *,
        access_token: str | SecretStr,
        account_id: UUID,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method not in _READ_METHODS | _WRITE_METHODS:
            raise VKInputError("unsupported", "Unsupported VK method")
        if method in _WRITE_METHODS:
            from dropgrid.integrations.vk.read_only import assert_write_allowed

            assert_write_allowed(method)
            self.require_write(method)
        token = (
            access_token.get_secret_value() if isinstance(access_token, SecretStr) else access_token
        )
        if not token.strip():
            raise VKInputError(method, "Access token is required")
        values = dict(params or {})
        if any(key.lower() in {"access_token", "v", "authorization"} for key in values):
            raise VKInputError(method, "Reserved VK request parameter")
        # A shared injected client must not smuggle auth via its default query/header settings.
        if self.http.params or self.http.auth is not None or "authorization" in self.http.headers:
            raise VKInputError(
                method, "HTTP client must not define default parameters or authorization"
            )
        form = {key: self._form_value(value) for key, value in values.items() if value is not None}
        form.update(access_token=token, v=self.settings.vk_api_version)
        attempts = self.settings.vk_max_attempts if method in _READ_METHODS else 1
        for attempt in range(1, attempts + 1):
            await self.limiter.acquire(account_id)
            started = time.monotonic()
            error: VKError | None = None
            payload: dict[str, Any] | None = None
            transient = False
            try:
                async with self.read_slots:
                    response = await self.http.post(
                        f"https://api.vk.com/method/{method}",
                        data=form,
                        timeout=self.settings.vk_timeout_seconds,
                        follow_redirects=False,
                    )
            except (httpx.TimeoutException, httpx.NetworkError):
                error = VKTransportError(method, "VK connection or timeout failure")
                transient = True
            except httpx.HTTPError:
                error = VKTransportError(method, "VK HTTP transport failure")
            else:
                if response.status_code >= 500:
                    error = VKTransportError(method, "VK HTTP server failure")
                    transient = True
                elif response.status_code == 429:
                    error = VKRateLimitError(method, "VK HTTP rate limit")
                elif response.status_code != 200:
                    error = VKTransportError(method, "VK HTTP request rejected")
                else:
                    try:
                        raw = response.json()
                    except ValueError:
                        raw = None
                    if not isinstance(raw, dict):
                        error = VKProtocolError(method, "Unexpected VK response envelope")
                    elif "error" in raw:
                        vk_error = raw["error"]
                        code = vk_error.get("error_code") if isinstance(vk_error, dict) else None
                        if type(code) is int:
                            error = api_error(method, code)
                            subcode = vk_error.get("error_subcode")
                            if type(subcode) is int and 0 <= subcode <= 2**31 - 1:
                                error.subcode = subcode
                            transient = code == 10
                        else:
                            error = VKProtocolError(method, "Unexpected VK error structure")
                    elif "response" not in raw:
                        error = VKProtocolError(method, "VK response field is missing")
                    elif self._contains_secret(raw["response"], token):
                        error = VKProtocolError(
                            method, "VK response unexpectedly included credentials"
                        )
                    else:
                        # Preserve only response; discard any unexpected request/debug data.
                        payload = {"response": raw["response"]}
            logger.info(
                "VK request",
                extra={
                    "vk_method": method,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    "success": error is None,
                    "error_code": error.code if error else None,
                    "attempt": attempt,
                },
            )
            if error is None and payload is not None:
                return payload
            if transient and attempt < attempts:
                delay = self.backoff(attempt)
                await self.sleeper(max(0, min(delay, 5)))
                continue
            # Raise outside upstream except blocks: no credential-bearing exception context.
            assert error is not None
            raise error
        raise VKAPIError(method, "VK attempt budget exhausted")

    @staticmethod
    def _contains_secret(value: object, token: str) -> bool:
        pending = [value]
        while pending:
            current = pending.pop()
            if isinstance(current, str) and token in current:
                return True
            if isinstance(current, dict):
                pending.extend(current.keys())
                pending.extend(current.values())
            elif isinstance(current, list):
                pending.extend(current)
        return False

    @staticmethod
    def _form_value(value: Any) -> str:
        if isinstance(value, bool):
            return str(int(value))
        if isinstance(value, (list, tuple)):
            return ",".join(str(item) for item in value)
        return str(value)

    async def get_current_user(self, *, access_token: str | SecretStr, account_id: UUID) -> VKUser:
        data = await self.call("users.get", access_token=access_token, account_id=account_id)
        users = parse_response(TypeAdapter(list[VKUser]), data["response"], "users.get")
        if len(users) != 1:
            raise VKProtocolError("users.get", "Expected a single current user")
        return users[0]

    async def get_notifications(
        self,
        *,
        access_token: str | SecretStr,
        account_id: UUID,
        count: int = 100,
        start_time: int | None = None,
        end_time: int | None = None,
        start_from: str | None = None,
        filters: list[str] | None = None,
    ) -> VKNotifications:
        if type(count) is not int or not 1 <= count <= 100:
            raise VKInputError("notifications.get", "Invalid notification count")
        for value in (start_time, end_time):
            if value is not None and (type(value) is not int or value < 0):
                raise VKInputError("notifications.get", "Invalid notification timestamp")
        if start_time is not None and end_time is not None and start_time > end_time:
            raise VKInputError("notifications.get", "Invalid notification time window")
        if start_from is not None and (
            not isinstance(start_from, str) or not start_from or len(start_from) > 4096
        ):
            raise VKInputError("notifications.get", "Invalid notification pagination token")
        allowed_filters = {
            "wall",
            "mentions",
            "comments",
            "likes",
            "reposted",
            "followers",
            "friends",
        }
        if filters is not None and (
            not isinstance(filters, list)
            or not filters
            or any(not isinstance(value, str) or value not in allowed_filters for value in filters)
        ):
            raise VKInputError("notifications.get", "Invalid notification filters")
        data = await self.call(
            "notifications.get",
            access_token=access_token,
            account_id=account_id,
            params={
                "count": count,
                "start_time": start_time,
                "end_time": end_time,
                "start_from": start_from,
                "filters": filters,
            },
        )
        return parse_response(TypeAdapter(VKNotifications), data["response"], "notifications.get")

    async def _groups(
        self, params: Mapping[str, Any], *, access_token: str | SecretStr, account_id: UUID
    ) -> list[VKCommunity]:
        data = await self.call(
            "groups.getById", access_token=access_token, account_id=account_id, params=params
        )
        response = data["response"]
        if not isinstance(response, dict):
            raise VKProtocolError("groups.getById", "Expected groups response object")
        return parse_response(
            TypeAdapter(list[VKCommunity]), response.get("groups"), "groups.getById"
        )

    @staticmethod
    def _resolution(reference: str, group: VKCommunity | None) -> CommunityResolution:
        status: ResolutionStatus = (
            "not_found"
            if group is None
            else (
                "deactivated"
                if group.deactivated
                else "private_or_unavailable"
                if group.is_closed and not group.is_member and not group.is_admin
                else "resolved"
            )
        )
        return CommunityResolution(reference=reference, status=status, group=group)

    @staticmethod
    def _resolution_failure(reference: str, error: VKError) -> CommunityResolution:
        if isinstance(error, (VKAuthenticationError, VKCaptchaRequiredError)):
            raise error
        status: ResolutionStatus = (
            "private_or_unavailable"
            if isinstance(error, VKPermissionError)
            else ("not_found" if error.code == 100 else "transient_error")
        )
        return CommunityResolution(reference=reference, status=status, error_code=error.code)

    async def resolve_communities(
        self, references: list[str], *, access_token: str | SecretStr, account_id: UUID
    ) -> list[CommunityResolution]:
        """Conservative chunks of 25, identity correlation, bounded singleton fallback.

        Never trust batch array order. Renamed/omitted aliases require a singleton.
        Transient batch failures do not fan out into a singleton request storm.
        """
        if (
            not isinstance(references, list)
            or not 1 <= len(references) <= 1000
            or any(not isinstance(ref, str) for ref in references)
        ):
            raise VKInputError("groups.getById", "Expected 1–1000 community references")
        try:
            normalized = [normalize_vk_community_reference(ref) for ref in references]
        except (ValueError, TypeError):
            raise VKInputError("groups.getById", "Invalid community reference") from None
        unique = list(dict.fromkeys(normalized))
        results: dict[str, CommunityResolution] = {}
        for start in range(0, len(unique), 25):
            chunk = unique[start : start + 25]
            try:
                groups = await self._groups(
                    {"group_ids": chunk}, access_token=access_token, account_id=account_id
                )
            except VKError as error:
                # Invalid/dead entries can reject a whole chunk. Only definitive
                # input/permission errors permit individual isolation.
                if error.code not in {100, 113, 15, 203}:
                    for ref in chunk:
                        results[ref] = self._resolution_failure(ref, error)
                    continue
                groups = []
            for ref in chunk:
                match = re.fullmatch(r"(?:club|public)?([0-9]+)", ref)
                numeric = match.group(1) if match else ""
                matches = [
                    g
                    for g in groups
                    if (numeric.isdigit() and g.id == int(numeric))
                    or (g.screen_name and g.screen_name.lower() == ref)
                ]
                if len(matches) == 1:
                    results[ref] = self._resolution(ref, matches[0])
                    continue
                try:
                    single = await self._groups(
                        {"group_id": ref}, access_token=access_token, account_id=account_id
                    )
                    if len(single) > 1 or single and numeric and single[0].id != int(numeric):
                        raise VKProtocolError("groups.getById", "Ambiguous singleton response")
                    results[ref] = self._resolution(ref, single[0] if single else None)
                except VKError as error:
                    results[ref] = self._resolution_failure(ref, error)
        return [results[ref] for ref in normalized]

    async def resolve_community(
        self, domain: str, *, access_token: str | SecretStr, account_id: UUID
    ) -> VKCommunity:
        error: VKInputError | None = None
        try:
            normalized = normalize_vk_community_reference(domain)
        except ValueError:
            error = VKInputError("groups.getById", "Invalid community reference")
        if error:
            raise error
        data = await self.call(
            "groups.getById",
            access_token=access_token,
            account_id=account_id,
            params={"group_id": normalized},
        )
        response = data["response"]
        if not isinstance(response, dict):
            raise VKProtocolError("groups.getById", "Expected groups response object")
        groups = parse_response(
            TypeAdapter(list[VKCommunity]), response.get("groups"), "groups.getById"
        )
        if not groups:
            raise VKCommunityUnavailableError("groups.getById", "Community not found")
        if len(groups) != 1:
            raise VKProtocolError("groups.getById", "Expected a single community")
        group = groups[0]
        if group.deactivated or (
            group.is_closed == 2 and not group.is_member and not group.is_admin
        ):
            raise VKCommunityUnavailableError("groups.getById", "Community unavailable or private")
        return group

    async def get_wall_posts(
        self,
        domain: str,
        *,
        access_token: str | SecretStr,
        account_id: UUID,
        count: int = 20,
        offset: int = 0,
        suggests: bool = False,
    ) -> WallPosts:
        if type(count) is not int or not 1 <= count <= 100 or type(offset) is not int or offset < 0:
            raise VKInputError("wall.get", "Invalid wall pagination")
        invalid: VKInputError | None = None
        try:
            normalized = normalize_vk_community_reference(domain)
        except ValueError:
            invalid = VKInputError("wall.get", "Invalid community reference")
        if invalid:
            raise invalid
        data = await self.call(
            "wall.get",
            access_token=access_token,
            account_id=account_id,
            params={
                "domain": normalized,
                "count": count,
                "offset": offset,
                "filter": "suggests" if suggests else "all",
                "extended": 0,
            },
        )
        return parse_response(TypeAdapter(WallPosts), data["response"], "wall.get")

    async def get_community_wall_history(
        self,
        group_id: int,
        *,
        access_token: str | SecretStr,
        account_id: UUID,
        count: int = 100,
        offset: int = 0,
    ) -> WallPosts:
        if (
            type(group_id) is not int
            or not 0 < group_id <= 2**63 - 1
            or type(count) is not int
            or not 1 <= count <= 100
            or type(offset) is not int
            or offset < 0
        ):
            raise VKInputError("wall.get", "Invalid community wall history request")
        data = await self.call(
            "wall.get",
            access_token=access_token,
            account_id=account_id,
            params={
                "owner_id": -group_id,
                "filter": "owner",
                "count": count,
                "offset": offset,
                "extended": 0,
            },
        )
        return parse_response(TypeAdapter(WallPosts), data["response"], "wall.get")

    @timed("archive_identity_refresh")
    async def get_wall_post_by_id(
        self, owner_id: int, post_id: int, *, access_token: str | SecretStr, account_id: UUID
    ) -> WallPostsById:
        if (
            type(owner_id) is not int
            or not 0 < abs(owner_id) <= 2**63 - 1
            or type(post_id) is not int
            or not 0 <= post_id <= 2**63 - 1
        ):
            raise VKInputError("wall.getById", "Invalid wall post identity")
        data = await self.call(
            "wall.getById",
            access_token=access_token,
            account_id=account_id,
            params={"posts": f"{owner_id}_{post_id}", "extended": 0},
        )
        return parse_response(TypeAdapter(WallPostsById), data["response"], "wall.getById")

    async def get_suggested_posts(
        self,
        domain: str,
        *,
        access_token: str | SecretStr,
        account_id: UUID,
        count: int = 20,
        offset: int = 0,
    ) -> WallPosts:
        return await self.get_wall_posts(
            domain,
            access_token=access_token,
            account_id=account_id,
            count=count,
            offset=offset,
            suggests=True,
        )

    async def create_wall_post(
        self, params: Mapping[str, Any], *, access_token: str | SecretStr, account_id: UUID
    ) -> WallPostReceipt:
        self.require_write()
        data = await self.call(
            "wall.post", access_token=access_token, account_id=account_id, params=params
        )
        return parse_response(TypeAdapter(WallPostReceipt), data["response"], "wall.post")
