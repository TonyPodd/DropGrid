from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_vk_client import NoWaitLimiter

from dropgrid.api.dependencies import token_provider, vk_client
from dropgrid.config import Settings
from dropgrid.db.models import Account
from dropgrid.integrations.vk.client import VKClient

pytestmark = pytest.mark.integration


class FakeTokens:
    async def get_token(self, account_id):
        return SecretStr("api-test-credential-sentinel")


def inject(client, vk):
    app = client._transport.app
    app.dependency_overrides[vk_client] = lambda: vk
    app.dependency_overrides[token_provider] = lambda: FakeTokens()


@pytest.mark.parametrize("invalid", [False, True])
async def test_account_validation_commit_and_token_safety(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], invalid
):
    account = (await client.post("/api/v1/accounts", json={"name": "Initial"})).json()
    async with sessions() as db, db.begin():
        entity = await db.get(Account, UUID(account["id"]))
        entity.encrypted_access_token = "not-plaintext-do-not-decrypt"
    response = (
        {"error": {"error_code": 5, "error_msg": "api-test-credential-sentinel"}}
        if invalid
        else {"response": [{"id": 123, "first_name": "Test", "last_name": "User"}]}
    )
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response)),
        limiter=NoWaitLimiter(),
    ) as vk:
        inject(client, vk)
        result = await client.post(f"/api/v1/accounts/{account['id']}/validate")
    assert result.status_code == 200 and result.json()["valid"] is not invalid
    assert (
        "api-test-credential-sentinel" not in result.text
        and "not-plaintext-do-not-decrypt" not in result.text
    )
    saved = (await client.get(f"/api/v1/accounts/{account['id']}")).json()
    assert saved["status"] == ("invalid" if invalid else "active")
    if not invalid:
        assert saved["vk_user_id"] == 123 and saved["name"] == "Test User"


async def test_resolve_updates_community_and_collision_rollback(client: httpx.AsyncClient):
    account = (await client.post("/api/v1/accounts", json={"name": "Demo"})).json()
    grid = (
        await client.post("/api/v1/grids/import", json={"name": "Grid", "text": "foo\nbar"})
    ).json()["grid"]
    communities = (await client.get(f"/api/v1/grids/{grid['id']}")).json()["communities"]
    response = {
        "response": {"groups": [{"id": 456, "name": "Resolved", "screen_name": "canonical"}]}
    }
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response)),
        limiter=NoWaitLimiter(),
    ) as vk:
        inject(client, vk)
        first = await client.post(
            f"/api/v1/communities/{communities[0]['id']}/resolve",
            json={"account_id": account["id"]},
        )
        assert first.status_code == 200 and first.json()["vk_group_id"] == 456
        assert first.json()["domain"] == "canonical" and first.json()["name"] == "Resolved"
        second = await client.post(
            f"/api/v1/communities/{communities[1]['id']}/resolve",
            json={"account_id": account["id"]},
        )
        assert second.status_code == 409
    saved = (await client.get(f"/api/v1/communities/{communities[1]['id']}")).json()
    assert saved["domain"] == communities[1]["domain"] and saved["vk_group_id"] is None


async def test_missing_provider_and_disabled_account_never_call_vk(client: httpx.AsyncClient):
    calls = []
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(lambda r: calls.append(r)),
        limiter=NoWaitLimiter(),
    ) as vk:
        client._transport.app.dependency_overrides[vk_client] = lambda: vk
        account = (await client.post("/api/v1/accounts", json={"name": "Demo"})).json()
        result = await client.post(f"/api/v1/accounts/{account['id']}/validate")
        assert result.status_code == 503
        await client.patch(f"/api/v1/accounts/{account['id']}", json={"status": "disabled"})
        result = await client.post(f"/api/v1/accounts/{account['id']}/validate")
        assert result.status_code == 409
    assert calls == []
