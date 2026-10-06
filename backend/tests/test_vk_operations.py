from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from test_vk_client import NoWaitLimiter

from dropgrid.config import Settings
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import DevelopmentTokenProvider
from dropgrid.integrations.vk.errors import (
    VKCommunityUnavailableError,
    VKCredentialUnavailableError,
    VKProtocolError,
)

ACCOUNT = UUID(int=1)


async def test_read_methods_current_user_resolve_and_suggests():
    seen = []

    def handle(request):
        seen.append(parse_qs(request.content.decode()))
        if request.url.path.endswith("users.get"):
            assert "user_ids" not in seen[-1]
            return httpx.Response(
                200, json={"response": [{"id": 123, "first_name": "Test", "last_name": "User"}]}
            )
        if request.url.path.endswith("groups.getById"):
            assert seen[-1]["group_id"] == ["example"]
            return httpx.Response(
                200,
                json={
                    "response": {
                        "groups": [{"id": 456, "name": "Group", "screen_name": "canonical"}]
                    }
                },
            )
        assert seen[-1]["filter"] == ["suggests"] and seen[-1]["domain"] == ["canonical"]
        return httpx.Response(200, json={"response": {"count": 0, "items": []}})

    async with VKClient(
        Settings(_env_file=None), transport=httpx.MockTransport(handle), limiter=NoWaitLimiter()
    ) as client:
        assert (
            await client.get_current_user(access_token="fake", account_id=ACCOUNT)
        ).display_name == "Test User"
        assert (
            await client.resolve_community("example", access_token="fake", account_id=ACCOUNT)
        ).screen_name == "canonical"
        assert (
            await client.get_suggested_posts("canonical", access_token="fake", account_id=ACCOUNT)
        ).count == 0


@pytest.mark.parametrize(
    "response",
    [
        {"response": {"groups": []}},
        {"response": {"groups": [{"id": 123, "is_closed": 2}]}},
        {"response": {"groups": [{"id": 123, "deactivated": "deleted"}]}},
        {"error": {"error_code": 203, "error_msg": "raw_secret"}},
        {"error": {"error_code": 15, "error_msg": "raw_secret"}},
    ],
)
async def test_community_unavailable(response):
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response)),
        limiter=NoWaitLimiter(),
    ) as client:
        with pytest.raises(VKCommunityUnavailableError):
            await client.resolve_community("example", access_token="fake", account_id=ACCOUNT)


async def test_protocol_error_does_not_echo_response():
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"response": [{"id": "sensitive"}]})
        ),
        limiter=NoWaitLimiter(),
    ) as client:
        with pytest.raises(VKProtocolError) as error:
            await client.get_current_user(access_token="fake", account_id=ACCOUNT)
    assert "sensitive" not in str(error.value) and error.value.__context__ is None


async def test_dev_provider_single_account_and_no_production():
    provider = DevelopmentTokenProvider(
        Settings(
            _env_file=None,
            app_env="development",
            vk_test_access_token="fake-sentinel",
            vk_test_account_id=ACCOUNT,
        )
    )
    assert (await provider.get_token(ACCOUNT)).get_secret_value() == "fake-sentinel"
    with pytest.raises(VKCredentialUnavailableError):
        await provider.get_token(UUID(int=2))
    prod = DevelopmentTokenProvider(
        Settings(
            _env_file=None,
            app_env="production",
            vk_test_access_token="fake-sentinel",
            vk_test_account_id=ACCOUNT,
        )
    )
    with pytest.raises(VKCredentialUnavailableError) as error:
        await prod.get_token(ACCOUNT)
    assert "fake-sentinel" not in str(error.value)
