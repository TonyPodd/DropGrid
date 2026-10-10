"""Grid category gender distribution, allocation by it and the strict account sequence."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_campaign_sender import mock, seed

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Campaign,
    Community,
    GridCategoryGender,
    GridCommunity,
    Submission,
)
from dropgrid.domain.enums import AccountStatus, CategoryGender, GenderTag, SubmissionStatus
from dropgrid.services.sending import preflight, start_campaign

pytestmark = pytest.mark.integration

GRID = "ГРУЗОВИКИ\n230 vk.com/truck1\n231 vk.com/truck2\n\nКРАСОТА\n232 vk.com/beauty1\n"


async def test_categories_are_detected_and_only_complete_distribution_saves(client):
    imported = await client.post(
        "/api/v1/grids/import", json={"name": "Октябрь", "text": "vk.com/orphan\n" + GRID}
    )
    gid = imported.json()["grid"]["id"]
    url = f"/api/v1/grids/{gid}/category-genders"
    read = (await client.get(url)).json()
    assert read["complete"] is False and read["updated_at"] is None
    assert [(c["category"], c["count"], c["gender"]) for c in read["categories"]] == [
        ("ГРУЗОВИКИ", 2, None),
        ("КРАСОТА", 1, None),
        (None, 1, None),
    ]
    partial = [{"category": "ГРУЗОВИКИ", "gender": "male"}]
    assert (await client.put(url, json={"items": partial})).status_code == 422
    unknown = [*partial, {"category": "КРАСОТА", "gender": "female"}, {"gender": "unisex"}]
    unknown.append({"category": "ДРУГОЕ", "gender": "unisex"})
    assert (await client.put(url, json={"items": unknown})).status_code == 422
    duplicate = [*unknown[:3], {"category": "КРАСОТА", "gender": "male"}]
    assert (await client.put(url, json={"items": duplicate})).status_code == 422
    assert (await client.put(url, json={"items": [{"gender": "other"}]})).status_code == 422
    saved = await client.put(url, json={"items": unknown[:3]})
    assert saved.status_code == 200 and saved.json()["complete"] is True
    assert {c["category"]: c["gender"] for c in saved.json()["categories"]} == {
        "ГРУЗОВИКИ": "male",
        "КРАСОТА": "female",
        None: "unisex",
    }
    assert (await client.get(url)).json() == saved.json()
    missing = await client.get(f"/api/v1/grids/{uuid4()}/category-genders")
    assert missing.status_code == 404


async def test_other_grids_suggest_but_never_apply_a_distribution(client):
    first = (await client.post("/api/v1/grids/import", json={"name": "A", "text": GRID})).json()
    await client.put(
        f"/api/v1/grids/{first['grid']['id']}/category-genders",
        json={
            "items": [
                {"category": "ГРУЗОВИКИ", "gender": "male"},
                {"category": "КРАСОТА", "gender": "female"},
            ]
        },
    )
    text = "Грузовики\n240 vk.com/truck9\n\nНОВАЯ\n241 vk.com/new1\n"
    second = (await client.post("/api/v1/grids/import", json={"name": "B", "text": text})).json()
    read = (await client.get(f"/api/v1/grids/{second['grid']['id']}/category-genders")).json()
    assert read["complete"] is False
    assert [(c["category"], c["gender"], c["suggested_gender"]) for c in read["categories"]] == [
        ("Грузовики", None, "male"),
        ("НОВАЯ", None, None),
    ]


async def categorized(sessions, tmp_path, *, autos_gender=None):
    """Six targets: 2 male, 2 female, 2 unisex; male + female accounts with quota 3."""
    data = await seed(sessions, tmp_path, count=6)
    cid, male_id, _, ids, _ = data
    categories = ["АВТО", "АВТО", "КРАСОТА", "КРАСОТА", "ЮМОР", "ЮМОР"]
    async with sessions() as s, s.begin():
        campaign = await s.get(Campaign, cid)
        await s.execute(
            delete(GridCategoryGender).where(GridCategoryGender.grid_id == campaign.grid_id)
        )
        genders = {"КРАСОТА": CategoryGender.female, "ЮМОР": CategoryGender.unisex}
        if autos_gender:
            genders["АВТО"] = autos_gender
        s.add_all(
            GridCategoryGender(grid_id=campaign.grid_id, category=name, gender=gender)
            for name, gender in genders.items()
        )
        for row_id, category in zip(ids, categories, strict=True):
            row = await s.get(Submission, row_id)
            relation = await s.get(GridCommunity, (campaign.grid_id, row.community_id))
            relation.category = category
        (await s.get(Account, male_id)).campaign_send_quota = 3
        female = Account(
            name="Female",
            vk_user_id=2,
            encrypted_access_token="mock-only",
            gender_tag=GenderTag.female,
            status=AccountStatus.active,
            campaign_send_quota=3,
        )
        s.add(female)
        await s.flush()
        return data, female.id


async def test_preflight_asks_for_undistributed_categories(sessions, tmp_path):
    (cid, male_id, _, _, storage), female_id = await categorized(sessions, tmp_path)
    async with sessions() as s:
        report, _ = await preflight(
            s, cid, None, None, storage, Settings(_env_file=None), account_ids=[male_id, female_id]
        )
    assert report["ready"] is False
    assert report["category_unassigned"] == 2 and report["unassigned_categories"] == ["АВТО"]


async def test_start_orders_each_account_own_gender_then_unisex(sessions, tmp_path):
    data, female_id = await categorized(sessions, tmp_path, autos_gender=CategoryGender.male)
    cid, male_id, _, _, storage = data
    settings = Settings(_env_file=None)
    async with sessions() as s:
        report, _ = await preflight(
            s, cid, None, None, storage, settings, account_ids=[male_id, female_id]
        )
    assert report["ready"] is True and report["category_unassigned"] == 0
    assert [(a["account_id"], a["assigned"]) for a in report["assigned_per_account"]] == [
        (str(male_id), 3),
        (str(female_id), 3),
    ]
    async with sessions() as s, s.begin():
        await start_campaign(
            s, cid, None, None, storage, settings, account_ids=[male_id, female_id]
        )
    async with sessions() as s:
        rows = (
            await s.execute(
                select(Submission, Community)
                .join(Community)
                .where(Submission.campaign_id == cid)
                .order_by(Submission.send_order)
            )
        ).all()
        assert (await s.get(Campaign, cid)).account_id == male_id
    assert [(r.account_id, c.domain) for r, c in rows] == [
        (male_id, "target0"),
        (male_id, "target1"),
        (male_id, "target4"),
        (female_id, "target2"),
        (female_id, "target3"),
        (female_id, "target5"),
    ]
    assert [r.send_order for r, _ in rows] == list(range(6))


async def finish(sessions, row_id, status=SubmissionStatus.submitted):
    async with sessions() as s, s.begin():
        row = await s.get(Submission, row_id)
        row.status, row.vk_send_phase = status, "verified"
        row.vk_send_lease_token = row.vk_send_lease_until = None
        (await s.get(Account, row.account_id)).vk_next_send_at = None


async def test_sender_switches_account_only_when_previous_has_no_work(sessions, tmp_path):
    data, female_id = await categorized(sessions, tmp_path, autos_gender=CategoryGender.male)
    cid, male_id, _, ids, storage = data
    async with sessions() as s, s.begin():
        await start_campaign(
            s,
            cid,
            None,
            None,
            storage,
            Settings(_env_file=None),
            account_ids=[male_id, female_id],
        )
    sender, seen, _ = mock(sessions, storage)
    order = []
    first = await sender._claim(None)
    order.append(first.id)
    # The male account is now paced; the female account must still wait for it.
    assert await sender._claim(None) is None
    await finish(sessions, first.id)
    second = await sender._claim(None)
    order.append(second.id)
    await finish(sessions, second.id, SubmissionStatus.failed)
    third = await sender._claim(None)
    order.append(third.id)
    async with sessions() as s, s.begin():
        # A receipt awaiting read-back never holds the next account back.
        row = await s.get(Submission, third.id)
        row.vk_send_phase, row.vk_send_receipt_post_id = "readback_pending", 42
        row.vk_send_lease_token = row.vk_send_lease_until = None
    fourth = await sender._claim(None)
    assert fourth.id == third.id and fourth.vk_send_phase == "readback_pending"
    await finish(sessions, fourth.id)
    for _ in range(3):
        claim = await sender._claim(None)
        order.append(claim.id)
        await finish(sessions, claim.id)
    assert order == [ids[0], ids[1], ids[4], ids[2], ids[3], ids[5]]
    assert seen == []
