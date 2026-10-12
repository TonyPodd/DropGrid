from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Community,
    CommunityReferencePhoto,
    Grid,
    GridCommunity,
    PhotoReviewer,
    Submission,
    utcnow,
)
from dropgrid.db.models import CommunityDiscoveryCandidate as Candidate
from dropgrid.db.models import CommunityDiscoveryEvidence as Evidence
from dropgrid.db.models import CommunityDiscoveryRun as Run
from dropgrid.discovery.queries import Seed, build_queries
from dropgrid.discovery.scoring import (
    activity,
    centroid,
    final_score,
    topic_match,
    visual_similarity,
)
from dropgrid.discovery.service import Discovery, RunInput, approve
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import VKInputError, VKProtocolError
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.visual import VisualEmbedding
from dropgrid.services.catalog import ConflictError


class Tokens:
    async def get_token(self, id):
        return SecretStr("test-db-token")


class NoWait:
    async def acquire(self, id):
        pass


def test_clusters_are_bounded_and_have_seed_provenance():
    seeds = [Seed(uuid4(), "Авто", "24 регион") for _ in range(514)]
    queries = build_queries(seeds, 3)
    assert len(queries) == 3 and len({q["query"] for q in queries}) == 3
    assert all(len(q["seed_ids"]) == 514 for q in queries)
    assert any("красноярск" in q["query"] for q in queries)
    assert "bmw e60" in build_queries([Seed(uuid4(), "БМВ Е60", "ГРУППЫ СОНИ")])[0]["query"]
    assert len(build_queries([Seed(uuid4(), "Кировец", "ТРАКТОР")], 1)) == 1
    with pytest.raises(ValidationError):
        RunInput(grid_id=uuid4(), account_id=uuid4(), scope_type="region")


def test_deterministic_evidence_and_activity_lower_bounds():
    now = utcnow()
    posts = [
        {"date": int((now - timedelta(days=d)).timestamp()), "post_type": "post"}
        for d in [2, 40, 95]
    ]
    a = activity(posts, now)
    assert a["state"] == "active" and a["posts_last_30d"] == 1 and a["posts_last_90d"] == 2
    assert a["counts_are_sample_lower_bounds"]
    assert (
        activity(posts[1:], now)["state"] == "weak" and activity(posts[2:], now)["state"] == "dead"
    )
    one = final_score(1, None, None, 1, 1)
    multi = final_score(1, None, None, 1, 3)
    assert multi[0] > one[0] and multi[1] > one[1]
    assert topic_match("BMW E60", "БМВ Е60 клуб") == 1
    assert topic_match("BMW E60", "BMW E39 клуб") == 0
    vector = VisualEmbedding("fixture", 3, (1, 0, 0))
    other = VisualEmbedding("fixture", 3, (0, 1, 0))
    assert centroid([vector, vector]) == vector
    assert visual_similarity(vector, {"same": [vector], "different": [other]}) == [
        ("same", 1.0),
        ("different", 0.0),
    ]


async def test_groups_search_typed_bounds_protocol_and_read_allowlist():
    seen = []

    def handle(r):
        seen.append(r.url.path)
        form = parse_qs(r.content.decode())
        assert form["q"] == ["BMW E60"] and form["count"] == ["10"] and form["offset"] == ["0"]
        return httpx.Response(
            200,
            json={
                "response": {
                    "count": 1,
                    "items": [
                        {
                            "id": 123,
                            "name": "BMW",
                            "screen_name": "bmw",
                            "is_closed": 0,
                            "type": "group",
                        }
                    ],
                }
            },
        )

    async with VKClient(
        Settings(_env_file=None), transport=httpx.MockTransport(handle), limiter=NoWait()
    ) as vk:
        assert (
            await vk.search_communities(
                "BMW E60", access_token="fixture", account_id=uuid4(), count=10
            )
        ).items[0].id == 123
        for q, count in [("", 10), ("valid", 101)]:
            with pytest.raises(VKInputError):
                await vk.search_communities(
                    q, access_token="fixture", account_id=uuid4(), count=count
                )
    assert seen == ["/method/groups.search"]
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"response": {"count": -1, "items": []}})
        ),
        limiter=NoWait(),
    ) as vk:
        with pytest.raises(VKProtocolError):
            await vk.search_communities("BMW E60", access_token="fixture", account_id=uuid4())


async def fixture(sessions, tmp_path, *, regional=True, bound=3):
    async with sessions() as db, db.begin():
        grid = Grid(name="Discovery fixture")
        account = Account(name="Read only", vk_user_id=123, encrypted_access_token="fake")
        seed = Community(
            domain="seed", vk_group_id=100, name="Авто Красноярск" if regional else "Кировец"
        )
        reviewer = PhotoReviewer(display_name="Fixture")
        db.add_all([grid, account, seed, reviewer])
        await db.flush()
        db.add(
            GridCommunity(
                grid_id=grid.id,
                community_id=seed.id,
                category="24 регион" if regional else "ТРАКТОР",
            )
        )
        gid, aid, sid, rid = grid.id, account.id, seed.id, reviewer.id
    methods = []

    def handle(r):
        method = r.url.path.split("/")[-1]
        methods.append(method)
        assert method in {"users.get", "groups.search", "groups.getById", "wall.get"}
        if method == "users.get":
            response = [{"id": 123, "first_name": "Test", "last_name": "Fixture"}]
        elif method == "groups.search":
            response = {
                "count": 4,
                "items": [
                    {"id": i, "name": name, "screen_name": domain, "is_closed": 0, "type": "group"}
                    for i, name, domain in [
                        (100, "Авто Красноярск", "seed"),
                        (200, "Авто Красноярск", "good"),
                        (201, "Авто Новосибирск", "wrong"),
                        (202, "Авто Красноярск", "bounded"),
                    ]
                ],
            }
        elif method == "groups.getById":
            id = int(parse_qs(r.content.decode())["group_ids"][0])
            response = {
                "groups": [
                    {
                        "id": id,
                        "name": "Авто Красноярск" if id == 200 else "Авто Новосибирск",
                        "screen_name": "good" if id == 200 else "wrong",
                        "is_closed": 0,
                        "type": "group",
                    }
                ]
            }
        else:
            response = {
                "count": 1,
                "items": [
                    {
                        "id": 1,
                        "date": int(utcnow().timestamp()),
                        "post_type": "post",
                        "attachments": [],
                    }
                ],
            }
        return httpx.Response(200, json={"response": response})

    vk = VKClient(
        Settings(
            _env_file=None,
            community_discovery_max_queries=3,
            community_discovery_max_candidates=bound,
        ),
        transport=httpx.MockTransport(handle),
        limiter=NoWait(),
    )
    engine = SimpleNamespace(storage=LocalMediaStorage(tmp_path), embedder=None)
    return Discovery(sessions, vk, Tokens(), engine), gid, aid, sid, rid, methods


@pytest.mark.integration
async def test_durable_restart_dedup_sticky_rejection_region_filter_and_manual_approval(
    sessions, tmp_path
):
    service, gid, aid, sid, rid, methods = await fixture(sessions, tmp_path)
    async with service.client:
        run = await service.enqueue(RunInput(grid_id=gid, account_id=aid))
        with pytest.raises(ConflictError):
            await service.enqueue(RunInput(grid_id=gid, account_id=aid))
        assert await service.tick() == 1
        # Expired lease after a killed process: the next instance resumes persisted cursor.
        async with sessions() as db, db.begin():
            row = await db.get(Run, run["id"])
            row.state = "running"
            row.lease_token = uuid4()
            row.lease_until = utcnow() - timedelta(seconds=1)
        resumed = Discovery(sessions, service.client, Tokens(), service.engine)
        for _ in range(6):
            await resumed.tick()
        async with sessions() as db, db.begin():
            row = await db.get(Run, run["id"])
            assert row.state == "ready" and row.query_cursor == 3
            candidates = (await db.scalars(select(Candidate).order_by(Candidate.vk_group_id))).all()
            assert len(candidates) == 3 and candidates[0].classification == "already_in_grid"
            good, wrong = candidates[1:]
            assert good.analysis_state == "ready" and good.reason["region_status"] == "same_region"
            assert wrong.status == "unavailable" and wrong.reason["filter"] == "region_mismatch"
            assert len(good.found_by_queries) == 3 and good.graph_score > 0
            good.status = "rejected"
            good.classification = "previously_rejected"
            cid = good.id
        again = await resumed.enqueue(RunInput(grid_id=gid, account_id=aid))
        for _ in range(6):
            await resumed.tick()
        async with sessions() as db, db.begin():
            assert await db.scalar(select(func.count()).select_from(Candidate)) == 3
            good = await db.get(Candidate, cid)
            assert good.status == "rejected"
            good.status = "new"  # explicit operator restoration, not an automatic retry
            before = await db.scalar(select(func.count()).select_from(Submission))
            result = await approve(
                db,
                cid,
                category="124 регион",
                region_key="krasnoyarsk_krai",
                content_hint="авто",
                reviewer_id=rid,
                run_id=again["id"],
            )
            assert result["status"] == "approved"
            gc = (
                await db.scalars(
                    select(GridCommunity).where(
                        GridCommunity.grid_id == gid, GridCommunity.community_id != sid
                    )
                )
            ).one()
            assert gc.region_key == "krasnoyarsk_krai" and gc.discovery_provenance[
                "approved_by"
            ] == str(rid)
            assert await db.scalar(select(func.count()).select_from(Submission)) == before == 0
            assert await db.scalar(select(func.count()).select_from(CommunityReferencePhoto)) == 0
    assert methods.count("groups.search") == 6
    assert set(methods) <= {"users.get", "groups.search", "groups.getById", "wall.get"}


@pytest.mark.integration
async def test_candidate_bound_applies_to_existing_rows_across_runs(sessions, tmp_path):
    service, gid, aid, _, _, _ = await fixture(sessions, tmp_path, bound=1)
    async with service.client:
        for _ in range(2):
            run = await service.enqueue(RunInput(grid_id=gid, account_id=aid))
            for _ in range(5):
                await service.tick()
            async with sessions() as db:
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(Evidence)
                        .where(Evidence.run_id == run["id"])
                    )
                    == 1
                )


@pytest.mark.integration
async def test_visual_analysis_is_bounded_and_kept_in_discovery_cache(sessions, tmp_path):
    from photo_fixtures import image_bytes

    from dropgrid.photos.visual import FakeVisualEmbedder

    service, gid, aid, sid, _, _ = await fixture(sessions, tmp_path)
    embedder = FakeVisualEmbedder()
    downloads = []

    async def download(url):
        downloads.append(url)
        return image_bytes(seed=len(downloads))

    async def references(id):
        return [VisualEmbedding(embedder.model, 3, (1, 0, 0))], [], None

    service.engine.embedder = embedder
    service.engine.reference_downloader = SimpleNamespace(download=download)
    service.engine.visual = SimpleNamespace(references=references)

    def handle(r):
        method = r.url.path.split("/")[-1]
        if method == "users.get":
            response = [{"id": 123, "first_name": "Test", "last_name": "Fixture"}]
        elif method == "groups.search":
            response = {
                "count": 1,
                "items": [
                    {"id": 200, "name": "Авто Красноярск", "screen_name": "good", "is_closed": 0}
                ],
            }
        elif method == "groups.getById":
            response = {
                "groups": [
                    {"id": 200, "name": "Авто Красноярск", "screen_name": "good", "is_closed": 0}
                ]
            }
        elif method == "wall.get":
            response = {
                "count": 40,
                "items": [
                    {
                        "id": i,
                        "owner_id": -200,
                        "from_id": -200,
                        "post_type": "post",
                        "date": int(utcnow().timestamp()) - i,
                        "attachments": [
                            {
                                "type": "photo",
                                "photo": {
                                    "id": i,
                                    "owner_id": -200,
                                    "sizes": [
                                        {
                                            "width": 1000,
                                            "height": 1000,
                                            "url": f"https://sun9-1.userapi.com/{i}.jpg",
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                    for i in range(1, 41)
                ],
            }
        else:
            pytest.fail("Discovery attempted a non-read method")
        return httpx.Response(200, json={"response": response})

    await service.client.aclose()
    service.client = VKClient(
        service.settings, transport=httpx.MockTransport(handle), limiter=NoWait()
    )
    async with service.client:
        await service.enqueue(RunInput(grid_id=gid, account_id=aid))
        for _ in range(6):
            await service.tick()
    async with sessions() as db:
        c = (await db.scalars(select(Candidate))).one()
        assert c.reason["photos_analyzed"] == 8 and len(c.previews) == 6
        assert c.visual_centroid and c.visual_model == embedder.model and c.visual_dimensions == 3
        assert c.reason["closest_communities"][0]["community_id"] == str(sid)
        assert all(service.storage.path(p).is_file() for p in c.previews)
        assert await db.scalar(select(func.count()).select_from(CommunityReferencePhoto)) == 0
    assert len(downloads) == embedder.calls == 8


@pytest.mark.integration
async def test_http_inbox_reject_restore_and_reviewer_attribution(client, sessions, tmp_path):
    service, gid, aid, _, rid, _ = await fixture(sessions, tmp_path)
    async with service.client:
        run = await service.enqueue(RunInput(grid_id=gid, account_id=aid))
        for _ in range(6):
            await service.tick()
    response = await client.get(f"/api/v1/discovery/candidates?grid_id={gid}")
    assert response.status_code == 200
    c = response.json()["items"][0]
    assert c["url"] == "https://vk.com/good"
    for action, status in [("reject", "rejected"), ("restore", "new")]:
        response = await client.post(
            f"/api/v1/discovery/candidates/{c['id']}/decision",
            json={"action": action, "reviewer_id": str(rid)},
        )
        assert response.status_code == 200 and response.json()["status"] == status
    response = await client.get("/api/v1/activity")
    activity_row = next(a for a in response.json() if a["kind"] == "discovery")
    assert activity_row["state"] == "success" and activity_row["stage"] == "Ranking candidates"
    assert activity_row["target_url"] == f"/discovery?grid={gid}&run={run['id']}"
