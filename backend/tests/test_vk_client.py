import json
import logging
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest

from dropgrid.config import Settings
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import (
    VKAPIError,
    VKAuthenticationError,
    VKCaptchaRequiredError,
    VKCommunityUnavailableError,
    VKInputError,
    VKPermissionError,
    VKProtocolError,
    VKRateLimitError,
    VKTransportError,
    VKWriteDisabledError,
)
from dropgrid.integrations.vk.limiter import LocalRateLimiter
from dropgrid.logging import JsonFormatter

ACCOUNT = UUID(int=1)
TOKEN = "unit-test-credential-sentinel"


class NoWaitLimiter:
    def __init__(self):
        self.accounts = []

    async def acquire(self, account_id):
        self.accounts.append(account_id)


def settings(**kwargs):
    return Settings(_env_file=None, vk_min_interval_seconds=1, **kwargs)


async def test_form_version_pooling_and_close():
    seen = []

    def handle(request):
        assert request.method == "POST"
        assert request.url.host == "api.vk.com"
        assert not request.url.query
        form = parse_qs(request.content.decode())
        assert form["v"] == ["5.200"]
        assert form["access_token"] == [TOKEN]
        assert form["fields"] == ["first_name,last_name"]
        seen.append(request)
        return httpx.Response(200, json={"response": {"id": 123}, "debug": TOKEN})

    client = VKClient(
        settings(vk_api_version="5.200"),
        transport=httpx.MockTransport(handle),
        limiter=NoWaitLimiter(),
    )
    async with client:
        for _ in range(2):
            assert await client.call(
                "users.get",
                access_token=TOKEN,
                account_id=ACCOUNT,
                params={"fields": ["first_name", "last_name"]},
            ) == {"response": {"id": 123}}
        assert not client.http.is_closed
    assert client.http.is_closed and len(seen) == 2


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (5, VKAuthenticationError),
        (18, VKAuthenticationError),
        (6, VKRateLimitError),
        (9, VKRateLimitError),
        (29, VKRateLimitError),
        (7, VKPermissionError),
        (15, VKPermissionError),
        (20, VKPermissionError),
        (14, VKCaptchaRequiredError),
        (17, VKCaptchaRequiredError),
        (203, VKCommunityUnavailableError),
        (100, VKAPIError),
    ],
)
async def test_typed_errors_never_retry_or_leak(code, expected, caplog):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "error": {
                    "error_code": code,
                    "error_msg": TOKEN,
                    "request_params": [{"key": "access_token", "value": TOKEN}],
                    "redirect_uri": f"https://vk.com/?token={TOKEN}",
                }
            },
        )

    with caplog.at_level(logging.INFO):
        async with VKClient(
            settings(), transport=httpx.MockTransport(handle), limiter=NoWaitLimiter()
        ) as client:
            with pytest.raises(expected) as error:
                await client.call("users.get", access_token=TOKEN, account_id=ACCOUNT)
    assert len(calls) == 1
    assert error.value.code == code
    assert error.value.__context__ is None
    assert TOKEN not in str(error.value)
    assert TOKEN not in json.dumps(error.value.as_dict())
    assert TOKEN not in caplog.text
    for record in caplog.records:
        assert TOKEN not in JsonFormatter().format(record)
    vk_record = next(r for r in caplog.records if r.name.endswith("vk.client"))
    fields = json.loads(JsonFormatter().format(vk_record))
    assert fields["error_code"] == code and fields["success"] is False


@pytest.mark.parametrize("failure", ["timeout", "connection", "500", "vk10"])
async def test_retry_transient_with_injected_sleeper(failure):
    calls, delays = [], []

    def handle(request):
        calls.append(request)
        if len(calls) < 3:
            if failure == "timeout":
                raise httpx.ReadTimeout(TOKEN, request=request)
            if failure == "connection":
                raise httpx.ConnectError(TOKEN, request=request)
            if failure == "500":
                return httpx.Response(503, text=TOKEN)
            return httpx.Response(200, json={"error": {"error_code": 10, "error_msg": TOKEN}})
        return httpx.Response(200, json={"response": []})

    async def sleep(delay):
        delays.append(delay)

    limiter = NoWaitLimiter()
    async with VKClient(
        settings(), transport=httpx.MockTransport(handle), limiter=limiter, sleeper=sleep
    ) as client:
        assert await client.call("users.get", access_token=TOKEN, account_id=ACCOUNT) == {
            "response": []
        }
    assert delays == [0.25, 0.5] and limiter.accounts == [ACCOUNT] * 3


async def test_timeout_exhausts_without_secret_context(caplog):
    attempts = []

    def handle(request):
        attempts.append(request)
        raise httpx.ReadTimeout(TOKEN, request=request)

    async def sleep(delay):
        pass

    async with VKClient(
        settings(), transport=httpx.MockTransport(handle), limiter=NoWaitLimiter(), sleeper=sleep
    ) as client:
        with pytest.raises(VKTransportError) as error:
            await client.call("users.get", access_token=TOKEN, account_id=ACCOUNT)
    assert len(attempts) == 3
    assert error.value.__context__ is None and TOKEN not in str(error.value)
    assert TOKEN not in caplog.text and TOKEN not in repr(vars(error.value))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429),
        httpx.Response(403, text=TOKEN),
        httpx.Response(302, headers={"location": f"https://vk.com/?token={TOKEN}"}),
        httpx.Response(200, text=TOKEN),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"error": {"error_code": "5", "error_msg": TOKEN}}),
        httpx.Response(200, json={}),
    ],
)
async def test_bad_responses_are_bounded_and_sanitized(response):
    calls = []

    def handle(request):
        calls.append(request)
        return response

    async with VKClient(
        settings(), transport=httpx.MockTransport(handle), limiter=NoWaitLimiter()
    ) as client:
        with pytest.raises((VKTransportError, VKProtocolError, VKRateLimitError)) as error:
            await client.call("users.get", access_token=TOKEN, account_id=ACCOUNT)
    assert len(calls) == 1 and TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "method", ["wall.post", "photos.getWallUploadServer", "photos.saveWallPhoto", "wall.delete"]
)
async def test_generic_write_guard_before_http(method):
    def forbidden(request):
        pytest.fail("write guard made a network request")

    async with VKClient(
        settings(), transport=httpx.MockTransport(forbidden), limiter=NoWaitLimiter()
    ) as client:
        with pytest.raises((VKWriteDisabledError, VKInputError)):
            await client.call(method, access_token=TOKEN, account_id=ACCOUNT)


async def test_enabled_write_never_retries_uncertain_timeout():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout(TOKEN, request=request)

    async with VKClient(
        settings(vk_write_enabled=True),
        transport=httpx.MockTransport(handle),
        limiter=NoWaitLimiter(),
    ) as client:
        with pytest.raises(VKTransportError):
            await client.create_wall_post(
                {"owner_id": -123}, access_token=TOKEN, account_id=ACCOUNT
            )
    assert len(calls) == 1


async def test_reserved_parameters_and_injected_client_ownership():
    def forbidden(request):
        pytest.fail("reserved input reached network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        async with VKClient(settings(), http_client=http, limiter=NoWaitLimiter()) as client:
            with pytest.raises(VKInputError):
                await client.call(
                    "users.get", access_token=TOKEN, account_id=ACCOUNT, params={"v": "1"}
                )
        assert not http.is_closed


async def test_local_limiter_separates_accounts_without_real_sleep():
    now = [0.0]
    delays = []

    async def sleep(delay):
        delays.append(delay)
        now[0] += delay

    limiter = LocalRateLimiter(1, sleeper=sleep, clock=lambda: now[0])
    await limiter.acquire(ACCOUNT)
    await limiter.acquire(UUID(int=2))
    await limiter.acquire(ACCOUNT)
    await limiter.acquire(ACCOUNT)
    assert delays == [1, 1]


async def test_token_echo_in_success_response_is_rejected(caplog):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "response": [{"id": 1, "first_name": TOKEN}],
            },
        )
    )
    async with VKClient(settings(), transport=transport, limiter=NoWaitLimiter()) as client:
        with pytest.raises(VKProtocolError) as error:
            await client.get_current_user(access_token=TOKEN, account_id=ACCOUNT)
    assert TOKEN not in str(error.value) and TOKEN not in caplog.text


async def test_wall_post_zero_identifier_is_retained_without_claiming_publication():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "response": {"post_id": 0},
            },
        )
    )
    async with VKClient(
        settings(vk_write_enabled=True), transport=transport, limiter=NoWaitLimiter()
    ) as client:
        receipt = await client.create_wall_post(
            {"owner_id": -123}, access_token=TOKEN, account_id=ACCOUNT
        )
    assert receipt.post_id == 0
