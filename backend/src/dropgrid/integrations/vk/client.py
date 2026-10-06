import asyncio
import logging
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
    VKCommunityUnavailableError,
    VKError,
    VKInputError,
    VKProtocolError,
    VKRateLimitError,
    VKTransportError,
    VKWriteDisabledError,
    api_error,
)
from dropgrid.integrations.vk.limiter import LocalRateLimiter, Sleeper, VKRateLimiter
from dropgrid.integrations.vk.models import VKCommunity, VKUser, WallPostReceipt, WallPosts

_READ_METHODS = frozenset({"users.get", "groups.getById", "wall.get"})
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
