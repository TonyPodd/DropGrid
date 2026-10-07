"""Exercise the actual Alembic chain against nonempty legacy media data."""

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


async def test_upgrade_keeps_legacy_media(database_url):
    name = "dropgrid_migration_" + uuid4().hex[:12]
    base = make_url(database_url)
    url = base.set(database=name).render_as_string(hide_password=False)
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(url)

    def migrate(revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            env={**os.environ, "DATABASE_URL": url},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, "Alembic upgrade failed"

    try:
        await asyncio.to_thread(migrate, "df050378e2c3")
        ids = [uuid4(), uuid4()]
        async with engine.begin() as conn:
            for asset_id in ids:
                await conn.execute(
                    text(
                        "INSERT INTO media_assets "
                        "(id,created_at,storage_key,category,tags,usage_count,enabled) "
                        "VALUES (:id,now(),'legacy.jpg','ГРУЗОВИКИ',ARRAY['legacy'],7,true)"
                    ),
                    {"id": asset_id},
                )
        await asyncio.to_thread(migrate, "head")
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id,storage_key,category,tags,usage_count,sha256,provider,"
                        "requires_publication_attribution FROM media_assets ORDER BY id"
                    )
                )
            ).all()
            assert len(rows) == 2
            assert {r.id for r in rows} == set(ids)
            assert all(
                r.storage_key == "legacy.jpg"
                and r.category == "ГРУЗОВИКИ"
                and r.tags == ["legacy"]
                and r.usage_count == 7
                and r.sha256 is None
                and r.provider is None
                and not r.requires_publication_attribution
                for r in rows
            )
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
