from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.api.app import create_app
from dropgrid.config import Settings
from dropgrid.db.models import Account

pytestmark = pytest.mark.integration


async def test_health_and_catalog_smoke(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    for endpoint in ("accounts", "communities", "grids", "campaigns"):
        assert (await client.get(f"/api/v1/{endpoint}")).json() == []
        assert (await client.get(f"/api/v1/{endpoint}/{uuid4()}")).status_code == 404
    assert (await client.get("/api/v1/accounts?limit=201")).status_code == 422


async def test_accounts_never_expose_credentials(
    client: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    response = await client.post("/api/v1/accounts", json={"name": "Demo"})
    assert response.status_code == 201
    account_id = response.json()["id"]
    async with sessions() as db, db.begin():
        account = await db.get(Account, account_id)
        assert account is not None
        account.encrypted_access_token = "sentinel-encrypted-value"
    for path in ("/api/v1/accounts", f"/api/v1/accounts/{account_id}"):
        response = await client.get(path)
        assert "sentinel-encrypted-value" not in response.text
        assert "encrypted_access_token" not in response.text
    response = await client.patch(f"/api/v1/accounts/{account_id}", json={"status": "disabled"})
    assert response.json()["status"] == "disabled"
    assert (
        await client.patch(f"/api/v1/accounts/{account_id}", json={"name": None})
    ).status_code == 422
    assert (
        await client.post("/api/v1/accounts", json={"name": "X", "access_token": "fake"})
    ).status_code == 422


async def test_parse_preview_and_import_reuse(client: AsyncClient) -> None:
    text = "CATEGORY\n1 vk.com/foo\n2 public123\n3 bad link!"
    parsed = await client.post("/api/v1/grids/parse", json={"text": text})
    assert parsed.status_code == 200
    assert len(parsed.json()["items"]) == 2
    assert len(parsed.json()["errors"]) == 1
    assert (await client.get("/api/v1/grids")).json() == []
    for name in ("First", "Second"):
        response = await client.post("/api/v1/grids/import", json={"name": name, "text": text})
        assert response.status_code == 201
        grid_id = response.json()["grid"]["id"]
        assert len((await client.get(f"/api/v1/grids/{grid_id}")).json()["communities"]) == 2
    communities = (await client.get("/api/v1/communities")).json()
    assert len(communities) == 2
    assert next(c for c in communities if c["domain"] == "club123")["vk_group_id"] == 123
    assert (
        await client.post("/api/v1/grids/import", json={"name": "Bad", "text": "1 bad!"})
    ).status_code == 422
    assert len((await client.get("/api/v1/grids")).json()) == 2


async def test_campaign_api(client: AsyncClient) -> None:
    grid = (
        await client.post("/api/v1/grids/import", json={"name": "Grid", "text": "foo\nbar"})
    ).json()["grid"]
    payload = {"name": "Campaign", "grid_id": grid["id"], "track_url": "https://vk.com/audio-1_2"}
    response = await client.post("/api/v1/campaigns", json=payload)
    assert response.status_code == 201
    campaign = response.json()
    assert campaign["publication_check_hours"] == 72
    assert campaign["status"] == "draft"
    path = f"/api/v1/campaigns/{campaign['id']}"
    updated = await client.patch(path, json={"publication_check_hours": 24, "caption": "Caption"})
    assert updated.json()["publication_check_hours"] == 24
    assert (await client.patch(path, json={"publication_check_hours": 0})).status_code == 422
    first = (await client.post(path + "/prepare")).json()
    second = (await client.post(path + "/prepare")).json()
    assert first["created"] == first["total"] == 2
    assert second["created"] == 0 and second["total"] == 2
    assert (await client.get(path)).json()["status"] == "ready"
    assert (await client.patch(path, json={"caption": "Changed"})).status_code == 409
    assert (
        await client.post("/api/v1/campaigns", json={**payload, "grid_id": str(uuid4())})
    ).status_code == 404
    assert (
        await client.post(
            "/api/v1/campaigns", json={**payload, "track_url": "https://evil.com/foo"}
        )
    ).status_code == 422


async def test_health_database_failure() -> None:
    app = create_app(
        Settings(
            _env_file=None, database_url="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"
        )
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


async def test_rejected_credentials_are_not_echoed(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/accounts",
        json={
            "name": "Demo",
            "encrypted_access_token": "credential-sentinel",
        },
    )
    assert response.status_code == 422
    assert "credential-sentinel" not in response.text
    response = await client.patch(
        f"/api/v1/accounts/{uuid4()}",
        json={
            "access_token": "credential-sentinel",
        },
    )
    assert response.status_code == 422
    assert "credential-sentinel" not in response.text
