"""Frozen migration backfills explicit region metadata without changing categories."""

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


async def test_region_backfill_preserves_grid_text_and_unknown_codes(database_url):
    name = "dropgrid_region_" + uuid4().hex[:12]
    url = make_url(database_url).set(database=name).render_as_string(hide_password=False)
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
        assert result.returncode == 0, "Region migration failed"

    try:
        await asyncio.to_thread(migrate, "03acecf962d2")
        gid = uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO grids (id,name,created_at,updated_at) "
                    "VALUES (:id,'Fixture',now(),now())"
                ),
                {"id": gid},
            )
            for i, category in enumerate(["24/124 регион", "999 регион", "BMW E24"]):
                cid = uuid4()
                await conn.execute(
                    text(
                        "INSERT INTO communities "
                        "(id,domain,category,is_active,resolution_status,created_at,updated_at) "
                        "VALUES (:id,:domain,:category,true,'unresolved',now(),now())"
                    ),
                    {"id": cid, "domain": f"fixture{i}", "category": category},
                )
                await conn.execute(
                    text(
                        "INSERT INTO grid_communities "
                        "(grid_id,community_id,category,comment,content_hint) "
                        "VALUES (:grid,:community,:category,'original comment','original hint')"
                    ),
                    {"grid": gid, "community": cid, "category": category},
                )
        await asyncio.to_thread(migrate, "head")
        async with engine.connect() as conn:
            rows = {
                r.category: r
                for r in (
                    await conn.execute(
                        text(
                            "SELECT category,comment,content_hint,region_key,region_codes,"
                            "discovery_provenance FROM grid_communities"
                        )
                    )
                ).all()
            }
            assert rows["24/124 регион"].region_key == "krasnoyarsk_krai" and rows[
                "24/124 регион"
            ].region_codes == ["24", "124"]
            assert rows["999 регион"].region_key is None and rows["999 регион"].region_codes == [
                "999"
            ]
            assert rows["BMW E24"].region_key is None and rows["BMW E24"].region_codes is None
            assert all(
                r.comment == "original comment"
                and r.content_hint == "original hint"
                and r.discovery_provenance is None
                for r in rows.values()
            )
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
