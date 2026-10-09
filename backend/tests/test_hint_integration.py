from uuid import uuid4

import httpx
import pytest
from photo_fixtures import candidate, image_bytes
from sqlalchemy import select
from test_reference_integration import seed

from dropgrid.config import Settings
from dropgrid.db.models import Grid, GridCommunity, MediaAsset
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import FakePhotoProvider, PhotoPolicy
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.preview import photo_preview
from dropgrid.photos.reference_schemas import PhotoPreviewInput
from dropgrid.photos.visual import FakeVisualEmbedder
from dropgrid.photos.visual_library import VisualLibrary

pytestmark = pytest.mark.integration


async def test_comment_import_hint_crud_grid_isolation(client, sessions):
    a = (
        await client.post(
            "/api/v1/grids/import",
            json={"name": "a", "text": "# МУЗЫКА\nvk.com/music.track124 — Д-П"},
        )
    ).json()
    b = (
        await client.post(
            "/api/v1/grids/import",
            json={"name": "b", "text": "# АВТО\nvk.com/music.track124 — машина"},
        )
    ).json()
    gid = a["grid"]["id"]
    other = b["grid"]["id"]
    members = (await client.get(f"/api/v1/grids/{gid}/communities")).json()["items"]
    assert members[0]["comment"] == "Д-П" and members[0]["content_hint"] is None
    cid = members[0]["id"]
    saved = await client.patch(
        f"/api/v1/grids/{gid}/communities/{cid}", json={"content_hint": "девушка с машиной"}
    )
    assert saved.status_code == 200 and saved.json()["comment"] == "Д-П"
    assert saved.json()["content_hint"] == "девушка с машиной"
    assert (await client.get(f"/api/v1/grids/{other}/communities")).json()["items"][0][
        "content_hint"
    ] is None
    assert (
        await client.patch(
            f"/api/v1/grids/{gid}/communities/{cid}", json={"content_hint": "x" * 501}
        )
    ).status_code == 422
    assert (
        await client.patch(f"/api/v1/grids/{uuid4()}/communities/{cid}", json={"comment": "no"})
    ).status_code == 404
    detail = (await client.get(f"/api/v1/grids/{gid}")).json()
    assert detail["communities"][0]["content_hint"] == "девушка с машиной"
    cleared = await client.patch(
        f"/api/v1/grids/{gid}/communities/{cid}", json={"content_hint": None}
    )
    assert cleared.json()["content_hint"] is None and cleared.json()["comment"] == "Д-П"


async def test_preview_query_provenance_and_comment_not_semanticized(sessions, tmp_path):
    cid, _ = await seed(sessions)
    async with sessions() as s, s.begin():
        g = Grid(name="hints")
        s.add(g)
        await s.flush()
        gid = g.id
        s.add(
            GridCommunity(
                grid_id=gid,
                community_id=cid,
                category="МУЗЫКА",
                comment="дембель",
                content_hint="девушка с машиной",
            )
        )
    provider = FakePhotoProvider((candidate(tags=("woman", "car")),))
    embedder = FakeVisualEmbedder()
    storage = LocalMediaStorage(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=image_bytes(), headers={"content-type": "image/jpeg"}
            )
        )
    ) as http:
        visual = VisualLibrary(sessions, storage, embedder)
        planner = CampaignMediaPlanner(
            sessions,
            SearchCache(sessions, provider),
            PhotoDownloader(http, PhotoPolicy()),
            storage,
            Settings(_env_file=None),
            visual=visual,
        )

        # Test fixture's safe CDN address is deliberately pinned without external DNS.
        async def resolver(host):
            return ["93.184.216.34"]

        planner.downloader.resolver = resolver
        preview = await photo_preview(
            planner, visual, cid, PhotoPreviewInput(grid_id=gid, candidate_limit=2)
        )
        assert preview.comment == "дембель" and preview.content_hint == "девушка с машиной"
        assert "woman car" in preview.generated_queries and not any(
            "soldier" in q for q in preview.generated_queries
        )
        assert len(provider.calls) <= 4
        assert len(preview.community_aware) == 1
        assert set(preview.community_aware[0].retrieval_queries) == set(preview.generated_queries)
        assert not preview.archive_shortlist
        async with sessions() as s:
            assert len((await s.scalars(select(MediaAsset))).all()) == 1


async def test_planner_separates_same_category_retrieval_contexts(sessions, tmp_path):
    from test_photo_integration import make_planner, prepared

    from dropgrid.photos.schemas import MediaPlanInput

    campaign_id = await prepared(sessions, count=2, category="МУЗЫКА")
    async with sessions() as s, s.begin():
        members = (
            await s.scalars(select(GridCommunity).order_by(GridCommunity.community_id))
        ).all()
        members[0].content_hint = "девушка с машиной"
        members[1].content_hint = "кот"
    provider = FakePhotoProvider((candidate(1), candidate(2)))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                content=image_bytes(seed=int(r.url.path.rsplit("/", 1)[1].split(".")[0])),
                headers={"content-type": "image/jpeg"},
            )
        )
    ) as http:
        result = await make_planner(sessions, tmp_path, provider, http).plan(
            campaign_id, MediaPlanInput()
        )
    queries = {q.query for q in provider.calls}
    assert "woman car" in queries and "cat" in queries
    assert len(provider.calls) <= 8 and result.newly_assigned == 2
    assert len(result.categories) == 1 and result.categories[0].assigned_count == 2
