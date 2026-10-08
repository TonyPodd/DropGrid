from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from test_vk_client import NoWaitLimiter

from dropgrid.api.dependencies import vk_client
from dropgrid.config import Settings
from dropgrid.db.models import Account
from dropgrid.domain.enums import AccountStatus
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import VKCredentialUnavailableError
from dropgrid.integrations.vk.token_storage import AccountTokenCipher, DBTokenProvider

TOKEN = "fake-imported-user-token-sentinel"


def cipher():
    return AccountTokenCipher(SecretStr(Fernet.generate_key().decode()))


def test_cipher_roundtrip_authenticated_account_binding():
    c = cipher()
    aid = uuid4()
    encrypted = c.encrypt(SecretStr(TOKEN), aid)
    assert encrypted != TOKEN and TOKEN not in encrypted and encrypted.startswith("fernet:v1:")
    assert c.decrypt(encrypted, aid).get_secret_value() == TOKEN
    for bad in (encrypted[:-4] + "XXXX", TOKEN, "fernet:v2:bad"):
        with pytest.raises(VKCredentialUnavailableError):
            c.decrypt(bad, aid)
    with pytest.raises(VKCredentialUnavailableError):
        c.decrypt(encrypted, uuid4())
    with pytest.raises(VKCredentialUnavailableError):
        cipher().decrypt(encrypted, aid)


@pytest.mark.parametrize("key", ["", "invalid", "é" * 44])
def test_cipher_bad_key_safe(key):
    c = AccountTokenCipher(SecretStr(key))
    with pytest.raises(VKCredentialUnavailableError) as e:
        c.encrypt(SecretStr(TOKEN), uuid4())
    assert TOKEN not in str(e.value)
    if key:
        assert key not in str(e.value)
    with pytest.raises(VKCredentialUnavailableError):
        c.decrypt("fernet:v1:bad", uuid4())


@pytest.mark.integration
@pytest.mark.parametrize("state", ["valid", "missing", "disabled", "invalid", "corrupt", "absent"])
async def test_db_provider(sessions, state):
    c = cipher()
    aid = uuid4()
    async with sessions() as db, db.begin():
        if state != "absent":
            a = Account(
                id=aid,
                name="Local",
                vk_user_id=123,
                status=AccountStatus.disabled
                if state == "disabled"
                else AccountStatus.invalid
                if state == "invalid"
                else AccountStatus.active,
            )
            a.encrypted_access_token = (
                None
                if state == "missing"
                else "bad"
                if state == "corrupt"
                else c.encrypt(SecretStr(TOKEN), aid)
            )
            db.add(a)
    p = DBTokenProvider(sessions, c)
    if state == "valid":
        assert (await p.get_token(aid)).get_secret_value() == TOKEN
    else:
        with pytest.raises(VKCredentialUnavailableError):
            await p.get_token(aid)


def configure(client, sessions, vk):
    app = client._transport.app
    app.state.token_cipher = cipher()
    app.state.token_provider = DBTokenProvider(sessions, app.state.token_cipher)
    app.dependency_overrides[vk_client] = lambda: vk


@pytest.mark.integration
async def test_import_replace_clear_and_capabilities(client, sessions):
    account = (await client.post("/api/v1/accounts", json={"name": "Local"})).json()
    aid = account["id"]
    calls = []

    def handle(r):
        method = r.url.path.split("/")[-1]
        calls.append(method)
        data = {
            "users.get": [{"id": 123, "first_name": "User", "last_name": "Name"}],
            "groups.getById": {"groups": [{"id": 242100737, "is_admin": 0, "is_member": 1}]},
            "wall.get": {"count": 0, "items": []},
        }
        return httpx.Response(200, json={"response": data[method]})

    async with VKClient(
        Settings(_env_file=None), transport=httpx.MockTransport(handle), limiter=NoWaitLimiter()
    ) as vk:
        configure(client, sessions, vk)
        for token in (TOKEN, TOKEN + "-replacement"):
            response = await client.put(
                f"/api/v1/accounts/{aid}/token", json={"access_token": token}
            )
            assert response.status_code == 200 and response.json() == {
                "account_id": aid,
                "vk_user_id": 123,
                "name": "User Name",
                "valid": True,
            }
            assert token not in response.text
            async with sessions() as db:
                a = await db.get(Account, UUID(aid))
                assert a.encrypted_access_token != token and token not in a.encrypted_access_token
            assert (await client.get(f"/api/v1/accounts/{aid}")).json()["token_configured"] is True
        result = await client.post(
            f"/api/v1/accounts/{aid}/vk/capabilities", json={"community_id": 242100737}
        )
        assert (
            result.json()["wall_read"]
            and result.json()["token_valid"]
            and not result.json()["is_admin"]
        )
        assert result.json()["write_capability"] == "UNTESTED" and calls == ["users.get"] * 2 + [
            "users.get",
            "groups.getById",
            "wall.get",
        ]
        assert TOKEN not in result.text
        cleared = await client.delete(f"/api/v1/accounts/{aid}/token")
        assert cleared.status_code == 200 and not cleared.json()["token_configured"]
        async with sessions() as db:
            a = await db.get(Account, UUID(aid))
            assert a.vk_user_id == 123 and a.encrypted_access_token is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "case", ["auth", "multiple", "empty", "mismatch", "key", "disabled", "production"]
)
async def test_import_failures_never_persist(client, sessions, case):
    account = (
        await client.post(
            "/api/v1/accounts",
            json={
                "name": "Original",
                "vk_user_id": 123,
                "status": "disabled" if case == "disabled" else "active",
            },
        )
    ).json()
    aid = account["id"]
    response = (
        {"error": {"error_code": 5, "error_msg": TOKEN}}
        if case == "auth"
        else {
            "response": []
            if case == "empty"
            else [{"id": 456}]
            if case == "mismatch"
            else [{"id": 123}, {"id": 456}]
            if case == "multiple"
            else [{"id": 123}]
        }
    )
    calls = []

    def handle(r):
        calls.append(r)
        return httpx.Response(200, json=response)

    async with VKClient(
        Settings(_env_file=None, app_env="production" if case == "production" else "development"),
        transport=httpx.MockTransport(handle),
        limiter=NoWaitLimiter(),
    ) as vk:
        configure(client, sessions, vk)
        if case == "key":
            client._transport.app.state.token_cipher = AccountTokenCipher(SecretStr(""))
        result = await client.put(f"/api/v1/accounts/{aid}/token", json={"access_token": TOKEN})
        assert result.status_code in (401, 409, 502, 503, 403) and TOKEN not in result.text
    async with sessions() as db:
        a = await db.get(Account, UUID(aid))
        assert a.encrypted_access_token is None and a.name == "Original" and a.vk_user_id == 123
    if case in ("key", "disabled", "production"):
        assert not calls


@pytest.mark.integration
async def test_failed_replacement_preserves_existing_credential(client, sessions):
    account = (
        await client.post("/api/v1/accounts", json={"name": "Original", "vk_user_id": 123})
    ).json()
    aid = UUID(account["id"])
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": {"error_code": 5, "error_msg": TOKEN}})
        ),
        limiter=NoWaitLimiter(),
    ) as vk:
        configure(client, sessions, vk)
        original = client._transport.app.state.token_cipher.encrypt(
            SecretStr("old-credential"), aid
        )
        async with sessions() as db, db.begin():
            a = await db.get(Account, aid)
            a.encrypted_access_token = original
        result = await client.put(f"/api/v1/accounts/{aid}/token", json={"access_token": TOKEN})
        assert result.status_code == 401 and TOKEN not in result.text
        async with sessions() as db:
            a = await db.get(Account, aid)
            assert a.encrypted_access_token == original and a.name == "Original"


@pytest.mark.integration
async def test_capability_permission_failure_is_read_only(client, sessions):
    account = (
        await client.post("/api/v1/accounts", json={"name": "User", "vk_user_id": 123})
    ).json()
    aid = UUID(account["id"])
    calls = []

    def handle(r):
        method = r.url.path.split("/")[-1]
        calls.append(method)
        return httpx.Response(
            200,
            json={"response": [{"id": 123}]}
            if method == "users.get"
            else {"error": {"error_code": 15, "error_subcode": 1134, "error_msg": TOKEN}},
        )

    async with VKClient(
        Settings(_env_file=None), transport=httpx.MockTransport(handle), limiter=NoWaitLimiter()
    ) as vk:
        configure(client, sessions, vk)
        async with sessions() as db, db.begin():
            a = await db.get(Account, aid)
            a.encrypted_access_token = client._transport.app.state.token_cipher.encrypt(
                SecretStr(TOKEN), aid
            )
        result = await client.post(
            f"/api/v1/accounts/{aid}/vk/capabilities", json={"community_id": 242100737}
        )
    data = result.json()
    assert data["token_valid"] and not data["community_resolved"] and not data["wall_read"]
    assert (
        data["error"]["error_subcode"] == 1134
        and TOKEN not in result.text
        and calls == ["users.get", "groups.getById"]
    )
