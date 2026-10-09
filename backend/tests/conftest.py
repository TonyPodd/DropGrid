import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dropgrid.api.app import create_app
from dropgrid.api.dependencies import session
from dropgrid.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime configuration must not leak from the developer's shell into tests."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set TEST_DATABASE_URL to a migrated, isolated PostgreSQL database")
    return value


@pytest_asyncio.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE submissions, campaigns, grid_communities, grids, "
                "communities, accounts, media_assets, photo_search_cache, "
                "photo_provider_state, photo_preview_cache CASCADE"
            )
        )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(
    sessions: async_sessionmaker[AsyncSession], database_url: str
) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(_env_file=None, database_url=database_url))

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as value, value.begin():
            yield value

    app.dependency_overrides[session] = test_session
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
            yield value


@pytest.fixture(autouse=True)
def forbid_external_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test must explicitly inject MockTransport/ASGITransport for HTTP."""
    import httpx

    async def denied_async(*args: object, **kwargs: object) -> None:
        pytest.fail("Real HTTP is disabled in tests; inject MockTransport")

    def denied_sync(*args: object, **kwargs: object) -> None:
        pytest.fail("Real HTTP is disabled in tests; inject MockTransport")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", denied_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied_sync)
