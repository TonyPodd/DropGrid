"""Upgrade nonempty legacy submissions without changing their status/data."""

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


async def test_upgrade_keeps_legacy_submission(database_url):
    name = "dropgrid_monitor_migration_" + uuid4().hex[:12]
    url = make_url(database_url).set(database=name).render_as_string(hide_password=False)
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        await c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(url)

    def migrate(command, revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", command, revision],
            env={**os.environ, "DATABASE_URL": url},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, "Migration failed"

    try:
        await asyncio.to_thread(migrate, "upgrade", "ead5fe0be527")
        gid, cid, campaign_id, sid = [uuid4() for _ in range(4)]
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO grids (id,name,created_at,updated_at) VALUES "
                    "(:id,'Legacy',now(),now())"
                ),
                {"id": gid},
            )
            await c.execute(
                text(
                    "INSERT INTO communities (id,domain,is_active,created_at,updated_at) VALUES "
                    "(:id,'legacy',true,now(),now())"
                ),
                {"id": cid},
            )
            await c.execute(
                text(
                    "INSERT INTO campaigns "
                    "(id,name,grid_id,track_url,status,publication_check_hours,created_at) "
                    "VALUES (:id,'Legacy',:gid,'https://vk.com/audio1_2','monitoring',72,now())"
                ),
                {"id": campaign_id, "gid": gid},
            )
            await c.execute(
                text(
                    "INSERT INTO submissions "
                    "(id,campaign_id,community_id,status,attempt_count,submitted_at,created_at,"
                    "updated_at) VALUES "
                    "(:id,:campaign,:community,'submitted',1,now(),now(),now())"
                ),
                {"id": sid, "campaign": campaign_id, "community": cid},
            )
        await asyncio.to_thread(migrate, "upgrade", "head")
        async with engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT "
                        "status,attempt_count,submitted_at,vk_suggested_post_id,"
                        "vk_published_post_id FROM submissions WHERE id=:id"
                    ),
                    {"id": sid},
                )
            ).one()
            assert (
                row.status == "submitted"
                and row.attempt_count == 1
                and row.submitted_at is not None
            )
            assert row.vk_suggested_post_id is None and row.vk_published_post_id is None
        await asyncio.to_thread(migrate, "downgrade", "ead5fe0be527")
        await asyncio.to_thread(migrate, "upgrade", "head")
    finally:
        await engine.dispose()
        async with admin.connect() as c:
            await c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
