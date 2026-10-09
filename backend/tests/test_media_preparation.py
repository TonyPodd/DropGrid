import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from photo_fixtures import image_bytes
from sqlalchemy import select
from test_reference_integration import Tokens, post, resolver, seed

from dropgrid.config import Settings
from dropgrid.db.models import (
    Community,
    CommunityReferencePhoto,
    Grid,
    GridCommunity,
    MediaPreparationJob,
    utcnow,
)
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.models import CommunityResolution
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.photos.preparation import MediaPreparation, PreparationInput
from dropgrid.photos.references import CommunityReferenceCollector, VKReferencePolicy
from dropgrid.photos.visual import FakeVisualEmbedder, serialize_embedding
from dropgrid.services.community_resolution import apply_resolution

pytestmark = pytest.mark.integration


async def setup(sessions, tmp_path, existing=0):
    cid, aid = await seed(sessions)
    settings = Settings(_env_file=None, campaign_reference_warmup_target=12)
    client = VKClient(
        settings,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"response": {"count": 12, "items": [post(i) for i in range(1, 13)]}}
            )
        ),
    )
    # Avoid waiting on the real limiter in deterministic fixture-only reads.
    client.limiter.acquire = AsyncMock()
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        )
    )
    storage = LocalMediaStorage(tmp_path)
    embedder = FakeVisualEmbedder()
    collector = CommunityReferenceCollector(
        sessions,
        client,
        Tokens(),
        PhotoDownloader(http, VKReferencePolicy(), resolver),
        storage,
        embedder,
    )
    async with sessions() as s, s.begin():
        c = await s.get(Community, cid)
        c.resolution_status = "resolved"
        grid = Grid(name="Real")
        s.add(grid)
        await s.flush()
        gid = grid.id
        s.add(GridCommunity(grid_id=gid, community_id=cid, category="cats", comment="Д-П"))
        image = normalize_image(image_bytes(), VKReferencePolicy())
        key = storage.write(image)
        embedding = await embedder.embed_image(image.data)
        for i in range(1, existing + 1):
            s.add(
                CommunityReferencePhoto(
                    community_id=cid,
                    vk_post_id=i,
                    vk_photo_owner_id=-123,
                    vk_photo_id=i,
                    posted_at=utcnow(),
                    width=1000,
                    height=1000,
                    source_url="https://sun9-1.userapi.com/a.jpg",
                    storage_key=key,
                    sha256=image.sha256,
                    perceptual_hash=image.perceptual_hash,
                    embedding=serialize_embedding(embedding),
                    embedding_model=embedder.model,
                    embedding_dimensions=3,
                )
            )
    return MediaPreparation(collector), gid, cid, aid, client, http


@pytest.mark.parametrize("existing", [0, 5, 12, 20])
async def test_warmup_idempotent_preserves_larger_sets(sessions, tmp_path, existing):
    prep, gid, cid, aid, client, http = await setup(sessions, tmp_path, existing)
    original = prep.collector.sync
    prep.collector.sync = AsyncMock(wraps=original)
    try:
        first = await prep.prepare_community_media_context(gid, cid, aid)
        second = await prep.prepare_community_media_context(gid, cid, aid)
        assert first.media_context_ready and second.media_context_ready
        assert second.reference_count == max(existing, 12)
        assert prep.collector.sync.call_count == int(existing < 12)
        assert client.limiter.acquire.call_count == int(existing < 12)
        assert first.comment == "Д-П" and first.content_hint is None
    finally:
        await client.aclose()
        await http.aclose()


async def test_readiness_queue_claim_retry_and_archive_disabled(sessions, tmp_path):
    prep, gid, cid, aid, client, http = await setup(sessions, tmp_path, 12)
    try:
        r = await prep.readiness(gid)
        assert r.total == r.resolved == r.references_ready == r.media_context_ready == 1
        assert r.with_comment == 1 and r.with_content_hint == r.archive_reuse_enabled == 0
        assert r.archive_optional and r.ready_to_create_campaign
        await prep.enqueue(gid, PreparationInput(account_id=aid))
        await prep.enqueue(gid, PreparationInput(account_id=aid))
        assert await prep.tick() == 1
        assert await prep.tick() == 0
        async with sessions() as s:
            rows = (await s.scalars(select(MediaPreparationJob))).all()
            assert len(rows) == 1 and rows[0].state == "ready"
            assert rows[0].result["archive_reuse_enabled"] is False
        assert client.limiter.acquire.call_count == 0
        await prep.enqueue(gid, PreparationInput(account_id=aid, retry_failed=True))
        assert await prep.tick() == 0
    finally:
        await client.aclose()
        await http.aclose()


async def test_independent_claims_expired_lease_and_safe_result(sessions, tmp_path):
    prep, gid, cid, aid, client, http = await setup(sessions, tmp_path, 12)
    try:
        await prep.enqueue(gid, PreparationInput(account_id=aid))
        claims = await asyncio.gather(prep.claim(), prep.claim())
        assert sum(x is not None for x in claims) == 1
        claimed = next(x for x in claims if x)
        async with sessions() as s, s.begin():
            row = await s.get(MediaPreparationJob, claimed[0])
            row.lease_until = utcnow() - timedelta(seconds=1)
        assert await prep.run_one()
        async with sessions() as s:
            row = await s.get(MediaPreparationJob, claimed[0])
            assert row.state == "ready" and row.attempts == 2
            assert row.lease_token is None
    finally:
        await client.aclose()
        await http.aclose()


async def test_transient_resolution_preserves_operator_active(sessions, tmp_path):
    prep, gid, cid, aid, client, http = await setup(sessions, tmp_path, 0)
    try:
        async with sessions() as s, s.begin():
            await apply_resolution(
                s,
                cid,
                CommunityResolution(reference="club123", status="transient_error", error_code=6),
            )
        async with sessions() as s:
            row = await s.get(Community, cid)
            assert (
                row.is_active
                and row.resolution_status == "transient_error"
                and row.vk_group_id == 123
            )
        readiness = await prep.readiness(gid)
        assert (
            readiness.transient == 1
            and readiness.unavailable == 0
            and not readiness.ready_to_create_campaign
        )
    finally:
        await client.aclose()
        await http.aclose()


async def test_bounded_context_concurrency(sessions, tmp_path):
    prep, gid, cid, aid, client, http = await setup(sessions, tmp_path, 12)
    running = peak = 0
    original = prep.context

    async def measured(*args):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        result = await original(*args)
        running -= 1
        return result

    prep.context = measured
    try:
        await asyncio.gather(
            *(prep.prepare_community_media_context(gid, cid, aid) for _ in range(8))
        )
        assert peak <= prep.settings.media_preparation_concurrency
    finally:
        await client.aclose()
        await http.aclose()


async def test_readiness_and_queue_api_are_bounded_and_do_not_run_jobs(client, sessions):
    cid, aid = await seed(sessions)
    imported = (
        await client.post(
            "/api/v1/grids/import", json={"name": "working", "text": "# Cats\nvk.com/club123 — Д-П"}
        )
    ).json()
    gid = imported["grid"]["id"]
    readiness = await client.get(f"/api/v1/grids/{gid}/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["total"] == readiness.json()["with_comment"] == 1
    assert readiness.json()["unresolved"] == 1 and not readiness.json()["ready_to_create_campaign"]
    response = await client.post(
        f"/api/v1/grids/{gid}/media-preparation", json={"account_id": str(aid)}
    )
    assert response.status_code == 202
    jobs = (await client.get(f"/api/v1/grids/{gid}/media-preparation")).json()
    assert (
        jobs["total"] == 1
        and jobs["items"][0]["state"] == "queued"
        and jobs["items"][0]["attempts"] == 0
    )
    assert (
        await client.post(
            f"/api/v1/grids/{gid}/resolve", json={"account_id": str(aid), "limit": 26}
        )
    ).status_code == 422
    assert (
        await client.post(
            f"/api/v1/grids/{gid}/media-preparation",
            json={"account_id": str(aid), "community_ids": [str(uuid4())]},
        )
    ).status_code == 409


async def test_ready_job_can_refresh_when_target_changes(sessions, tmp_path):
    prep, gid, cid, aid, client, http = await setup(sessions, tmp_path, 12)
    try:
        await prep.enqueue(gid, PreparationInput(account_id=aid))
        await prep.tick()
        prep.target = 13
        await prep.enqueue(gid, PreparationInput(account_id=aid))
        async with sessions() as s:
            job = await s.scalar(select(MediaPreparationJob))
            assert job.state == "queued"
        assert await prep.compatible_count(cid) == 12
        assert client.limiter.acquire.call_count == 0
    finally:
        await client.aclose()
        await http.aclose()
