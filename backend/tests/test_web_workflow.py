from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.api.app import create_app
from dropgrid.db.models import Campaign, Submission
from dropgrid.domain.enums import CampaignStatus, SubmissionStatus


async def import_grid(client: AsyncClient, text: str, name: str = "Grid") -> str:
    response = await client.post("/api/v1/grids/import", json={"name": name, "text": text})
    assert response.status_code == 201
    return str(response.json()["grid"]["id"])


async def campaign(client: AsyncClient, grid_id: str) -> str:
    response = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "Campaign",
            "grid_id": grid_id,
            "track_url": "https://vk.ru/audio-123_456",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


@pytest.mark.integration
async def test_grid_categories_and_pagination(client: AsyncClient) -> None:
    first = await import_grid(client, "FIRST\nfoo\nbar")
    second = await import_grid(client, "SECOND\nfoo\nTHIRD\nbar")
    for grid_id, expected in ((first, ["FIRST", "FIRST"]), (second, ["THIRD", "SECOND"])):
        detail = (await client.get(f"/api/v1/grids/{grid_id}")).json()
        assert detail["community_count"] == 2
        assert [c["category"] for c in detail["communities"]] == expected
        assert sum(c["count"] for c in detail["categories"]) == 2
    path = f"/api/v1/grids/{second}/communities"
    pages = [(await client.get(path, params={"page": n, "page_size": 1})).json() for n in (1, 2, 3)]
    assert [len(p["items"]) for p in pages] == [1, 1, 0]
    assert pages[0]["total"] == 2
    assert pages[0]["items"][0]["id"] != pages[1]["items"][0]["id"]
    assert (await client.get(path, params={"page": 0})).status_code == 422
    assert (await client.get(path, params={"page_size": 201})).status_code == 422
    summaries = (await client.get("/api/v1/grids")).json()
    assert next(g for g in summaries if g["id"] == second)["category_count"] == 2
    assert (await client.get(f"/api/v1/grids/{first}?limit=1")).json()["community_count"] == 2


@pytest.mark.integration
async def test_tracks_and_caption_preservation(client: AsyncClient) -> None:
    grid_id = await import_grid(client, "foo")
    payload = {
        "name": "  Campaign  ",
        "grid_id": grid_id,
        "track_url": "https://m.vk.ru/audio?z=audio-23_45/context",
        "caption": "  first\nlast  ",
        "track_owner_id": 999,
        "track_audio_id": 999,
    }
    result = await client.post("/api/v1/campaigns", json=payload)
    assert result.status_code == 201
    data = result.json()
    assert (data["track_owner_id"], data["track_audio_id"]) == (-23, 45)
    assert data["caption"] == payload["caption"] and data["name"] == "Campaign"
    path = f"/api/v1/campaigns/{data['id']}"
    updated = (
        await client.patch(path, json={"track_url": "https://vk.com/audio78_90", "caption": ""})
    ).json()
    assert updated["track_owner_id"] == 78 and updated["track_audio_id"] == 90
    assert updated["caption"] == ""
    for bad in (
        "https://vk.com/foo",
        "https://evil.com/audio1_2",
        "https://vk.ru/audio0_0",
        "https://vk.com/audio1_2?access_token=credential-sentinel",
    ):
        response = await client.post("/api/v1/campaigns", json={**payload, "track_url": bad})
        assert response.status_code == 422
        assert "credential-sentinel" not in response.text
        assert (await client.patch(path, json={"track_url": bad})).status_code == 422
    assert (await client.patch(path, json={"publication_check_hours": 0})).status_code == 422
    assert (await client.patch(path, json={"publication_check_hours": 721})).status_code == 200
    assert (
        await client.post("/api/v1/tracks/parse", json={"track_url": "https://vk.ru/audio78_90"})
    ).json() == {"owner_id": 78, "audio_id": 90}
    assert (await client.post("/api/v1/tracks/parse", json={"track_url": "bad"})).status_code == 422
    await client.post(path + "/prepare")
    for field, value in (
        ("caption", "x"),
        ("name", "x"),
        ("track_url", "https://vk.ru/audio2_3"),
        ("publication_check_hours", 3),
    ):
        assert (await client.patch(path, json={field: value})).status_code == 409


@pytest.mark.integration
async def test_submissions_filters_stats_and_safe_errors(
    client: AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    grid_id = await import_grid(
        client, "ALPHA\n" + "\n".join(f"community{i:02}" for i in range(26)) + "\nBETA\nlastone"
    )
    campaign_id = await campaign(client, grid_id)
    other_id = await campaign(client, grid_id)
    for cid in (campaign_id, other_id):
        assert (await client.post(f"/api/v1/campaigns/{cid}/prepare")).json()["total"] == 27
    async with sessions() as db, db.begin():
        rows = (
            await db.scalars(
                select(Submission)
                .where(Submission.campaign_id == campaign_id)
                .order_by(Submission.id)
            )
        ).all()
        rows[0].status = SubmissionStatus.failed
        rows[0].error_code = "credential-sentinel"
        rows[0].error_message = "Traceback access_token=credential-sentinel"
        rows[0].published_post_url = "https://evil.com/?token=credential-sentinel"
        rows[1].status = SubmissionStatus.published
        rows[1].published_post_url = "https://vk.ru/wall-123_456"
    path = f"/api/v1/campaigns/{campaign_id}"
    first = (await client.get(path + "/submissions")).json()
    second = (await client.get(path + "/submissions?page=2")).json()
    assert len(first["items"]) == 25 and len(second["items"]) == 2
    assert not set(i["id"] for i in first["items"]) & set(i["id"] for i in second["items"])
    other = (await client.get(f"/api/v1/campaigns/{other_id}/submissions")).json()
    assert not set(i["id"] for i in first["items"]) & set(i["id"] for i in other["items"])
    failed = await client.get(path + "/submissions?status=failed")
    assert failed.json()["total"] == 1
    assert "credential-sentinel" not in failed.text and "Traceback" not in failed.text
    published = (await client.get(path + "/submissions?status=published")).json()
    assert published["items"][0]["published_post_url"] == "https://vk.ru/wall-123_456"
    assert (await client.get(path + "/submissions?category=BETA")).json()["total"] == 1
    assert (await client.get(path + "/submissions?category=ALPHA&status=pending")).json()[
        "total"
    ] in (24, 25)
    stats = (await client.get(path + "/stats")).json()
    assert stats["total"] == 27
    assert stats["statuses"]["pending"] == 25 and stats["statuses"]["failed"] == 1
    assert (await client.post(path + "/prepare")).json()["created"] == 0
    assert (await client.get(path + "/stats")).json() == stats
    for suffix in ("submissions", "stats"):
        assert (await client.get(f"/api/v1/campaigns/{uuid4()}/{suffix}")).status_code == 404
    assert (await client.get(path + "/submissions?status=unknown")).status_code == 422
    assert (await client.get(path + "/submissions?page_size=201")).status_code == 422


@pytest.mark.integration
async def test_dashboard(client: AsyncClient, sessions: async_sessionmaker[AsyncSession]) -> None:
    grid_id = await import_grid(client, "foo\nbar")
    await client.post("/api/v1/accounts", json={"name": "Test account"})
    async with sessions() as db, db.begin():
        for status in CampaignStatus:
            db.add(
                Campaign(
                    name=str(status),
                    grid_id=grid_id,
                    track_url="https://vk.ru/audio1_2",
                    status=status,
                )
            )
    result = (await client.get("/api/v1/dashboard")).json()
    assert result["counts"] == {"accounts": 1, "communities": 2, "grids": 1, "campaigns": 7}
    assert result["campaign_statuses"] == {str(s): 1 for s in CampaignStatus}
    assert len(result["recent_campaigns"]) == 7
    assert all(
        c["grid_name"] == "Grid" and c["community_count"] == 2 and c["submission_count"] == 0
        for c in result["recent_campaigns"]
    )


def test_openapi_generation() -> None:
    document = create_app().openapi()
    for path in (
        "/api/v1/dashboard",
        "/api/v1/grids/{entity_id}/communities",
        "/api/v1/campaigns/{entity_id}/submissions",
        "/api/v1/campaigns/{entity_id}/stats",
    ):
        assert (
            "$ref"
            in document["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
                "schema"
            ]
        )


@pytest.mark.integration
async def test_parse_keeps_original_line_numbers(client: AsyncClient) -> None:
    preview = (
        await client.post("/api/v1/grids/parse", json={"text": "\n\nfoo\n4 bad link!\n"})
    ).json()
    assert preview["errors"][0]["line"] == 4
    assert preview["errors"][0]["value"] == "4 bad link!"
    assert (
        await client.post("/api/v1/grids/import", json={"text": "foo", "name": "   "})
    ).status_code == 422
