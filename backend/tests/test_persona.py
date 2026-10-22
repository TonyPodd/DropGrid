"""Dating/selfie publics: girl accounts, their own photos and per-public photo choice."""

from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from photo_fixtures import image_bytes
from sqlalchemy import select
from test_photo_integration import make_planner

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    AccountPersonaPhoto,
    Campaign,
    CampaignAccount,
    Community,
    CommunityReferencePhoto,
    Grid,
    GridCategoryGender,
    GridCommunity,
    MediaAsset,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import (
    AccountStatus,
    CampaignStatus,
    CategoryGender,
    GenderTag,
    SubmissionStatus,
)
from dropgrid.domain.personas import is_persona_category
from dropgrid.photos.domain import FakePhotoProvider
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.persona import text_signal
from dropgrid.photos.schemas import MediaPlanInput
from dropgrid.photos.visual import FakeVisualEmbedder, serialize_embedding
from dropgrid.photos.visual_library import VisualLibrary
from dropgrid.services import persona_photos
from dropgrid.services.account_pools import PERSONA_ACCOUNT_MISSING, distribute
from dropgrid.services.category_genders import Placement
from dropgrid.services.persona_photos import add_photo, remove_photo
from dropgrid.services.sending import preflight

DATING = Placement("ЗНАКОМСТВА", None, persona=True)
HUMOR = Placement("ЮМОР", CategoryGender.unisex)
FEMALE = Placement("КРАСОТА", CategoryGender.female)


def account(i, gender=GenderTag.female):
    return Account(
        id=UUID(int=i),
        name=str(i),
        vk_user_id=i,
        encrypted_access_token="mock",
        status=AccountStatus.active,
        gender_tag=gender,
    )


def targets(*placements):
    rows, places = [], {}
    for i, placement in enumerate(placements):
        community = Community(id=UUID(int=10_000 + i), domain=f"club{i:04}")
        rows.append((Submission(id=UUID(int=1_000 + i)), community))
        places[community.id] = placement
    return rows, places


@pytest.mark.parametrize(
    "name,expected",
    [
        ("ЗНАКОМСТВА", True),
        ("Знакомства 18+", True),
        ("СЕЛФИ ДЕВУШЕК", True),
        ("Selfie", True),
        ("ЮМОР", False),
        (None, False),
    ],
)
def test_persona_categories_are_recognised_by_name(name, expected):
    assert is_persona_category(name) is expected


def test_photo_tags_match_the_public_text():
    assert text_signal(["селфи"], "Селфи девчонок каждый день") == 1
    assert text_signal(["зеркало"], "Девушки в зеркале") == 1
    assert text_signal(["в зеркале", "селфи"], "Фото в зеркале") == 0.5
    assert text_signal(["пляж"], "Девушки в зеркале") == 0
    assert text_signal([], "Девушки") == 0


def test_girls_send_dating_publics_first_then_their_gender_then_unisex():
    rows, places = targets(DATING, DATING, FEMALE, HUMOR, HUMOR)
    girl, other = account(1), account(2)
    allocation, errors = distribute(
        rows, [(other, 100, 0), (girl, 3, 1)], places, persona={girl.id}
    )
    assert not errors
    by_row = {row.id: allocation[row.id] for row, _ in rows}
    assert [by_row[row.id] for row, _ in rows] == [girl.id, girl.id, other.id, other.id, other.id]
    # The girl's own lane comes first in the send order of her account.
    girl_rows = [row_id for row_id, owner in allocation.items() if owner == girl.id]
    assert girl_rows == [rows[0][0].id, rows[1][0].id]


def test_girl_continues_with_other_categories_after_her_publics():
    rows, places = targets(DATING, FEMALE, HUMOR)
    girl = account(1)
    allocation, errors = distribute(rows, [(girl, 100, 0)], places, persona={girl.id})
    assert not errors and list(allocation) == [row.id for row, _ in rows]


def test_dating_publics_need_a_girl_account():
    rows, places = targets(DATING, HUMOR)
    allocation, errors = distribute(rows, [(account(1), 100, 0)], places)
    assert list(allocation.values()) == [UUID(int=1)]
    assert list(errors.values()) == [PERSONA_ACCOUNT_MISSING]


def test_publics_holding_a_girls_photo_stay_with_her():
    rows, places = targets(DATING, DATING, DATING)
    first, second = account(1), account(2)
    pinned = {rows[0][0].id: second.id}
    allocation, errors = distribute(
        rows, [(first, 1, 0), (second, 5, 1)], places, {first.id, second.id}, pinned
    )
    assert not errors
    assert allocation[rows[0][0].id] == second.id
    assert allocation[rows[1][0].id] == first.id
    assert allocation[rows[2][0].id] == second.id


async def persona_campaign(sessions, tmp_path):
    """Two dating publics with different wall styles, one humor public, girl + boy."""
    storage = LocalMediaStorage(tmp_path)
    async with sessions() as db, db.begin():
        grid = Grid(name="Октябрь")
        girl = Account(
            name="Аня",
            vk_user_id=1,
            encrypted_access_token="mock",
            gender_tag=GenderTag.female,
            status=AccountStatus.active,
        )
        boy = Account(
            name="Петя",
            vk_user_id=2,
            encrypted_access_token="mock",
            gender_tag=GenderTag.male,
            status=AccountStatus.active,
        )
        db.add_all([grid, girl, boy])
        await db.flush()
        campaign = Campaign(
            name="Знакомства",
            grid_id=grid.id,
            track_url="https://vk.com/audio1_2",
            track_owner_id=1,
            track_audio_id=2,
            status=CampaignStatus.ready,
        )
        db.add(campaign)
        db.add(GridCategoryGender(grid_id=grid.id, category="ЮМОР", gender=CategoryGender.unisex))
        await db.flush()
        communities = {}
        for i, (domain, name, category) in enumerate(
            [
                ("selfies", "Красивые девушки", "ЗНАКОМСТВА"),
                ("mirrors", "Девушки в зеркале", "ЗНАКОМСТВА"),
                ("jokes", "Шутки", "ЮМОР"),
            ]
        ):
            community = Community(
                domain=domain,
                name=name,
                vk_group_id=500 + i,
                resolution_status="resolved",
            )
            db.add(community)
            await db.flush()
            db.add(GridCommunity(grid_id=grid.id, community_id=community.id, category=category))
            db.add(Submission(campaign_id=campaign.id, community_id=community.id))
            communities[domain] = community.id
        db.add_all(
            [
                CampaignAccount(
                    campaign_id=campaign.id, account_id=girl.id, priority=0, persona=True
                ),
                CampaignAccount(campaign_id=campaign.id, account_id=boy.id, priority=1),
            ]
        )
        selfie = await add_photo(db, girl.id, "selfie", image_bytes(seed=1), storage, [])
        mirror = await add_photo(db, girl.id, "other", image_bytes(seed=2), storage, ["зеркало"])
        selfie_asset = await db.get(MediaAsset, selfie.media_asset_id)
    # The selfie public's wall looks exactly like the girl's selfie.
    embedder = FakeVisualEmbedder()
    vector = await embedder.embed_image(storage.path(selfie_asset.storage_key).read_bytes())
    async with sessions() as db, db.begin():
        for i in range(2):
            db.add(
                CommunityReferencePhoto(
                    community_id=communities["selfies"],
                    vk_post_id=i + 1,
                    vk_photo_owner_id=-500,
                    vk_photo_id=i + 1,
                    posted_at=utcnow() - timedelta(days=1),
                    embedding=serialize_embedding(vector),
                    embedding_model=embedder.model,
                    embedding_dimensions=embedder.dimensions,
                )
            )
    return {
        "campaign": campaign.id,
        "girl": girl.id,
        "boy": boy.id,
        "selfie": selfie.media_asset_id,
        "mirror": mirror.media_asset_id,
        "communities": communities,
        "storage": storage,
        "embedder": embedder,
    }


def persona_planner(sessions, tmp_path, client, embedder):
    planner = make_planner(sessions, tmp_path, FakePhotoProvider(()), client)
    planner.visual = VisualLibrary(sessions, LocalMediaStorage(tmp_path), embedder)
    return planner


async def submission_for(db, campaign_id, community_id):
    return await db.scalar(
        select(Submission).where(
            Submission.campaign_id == campaign_id, Submission.community_id == community_id
        )
    )


@pytest.mark.integration
async def test_each_dating_public_gets_the_girls_best_matching_photo(sessions, tmp_path):
    data = await persona_campaign(sessions, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
    ) as client:
        planner = persona_planner(sessions, tmp_path, client, data["embedder"])
        report = await planner.plan(data["campaign"], MediaPlanInput())
    assert report.persona_assigned == 2 and report.persona_missing == 0
    async with sessions() as db:
        selfies = await submission_for(db, data["campaign"], data["communities"]["selfies"])
        mirrors = await submission_for(db, data["campaign"], data["communities"]["mirrors"])
        jokes = await submission_for(db, data["campaign"], data["communities"]["jokes"])
        # Wall style picks the selfie; with no studied wall, the name picks the mirror.
        assert selfies.media_asset_id == data["selfie"]
        assert mirrors.media_asset_id == data["mirror"]
        assert jokes.media_asset_id is None
        assert selfies.photo_source == "persona"
        session = await db.scalar(
            select(PhotoSelectionSession).where(PhotoSelectionSession.submission_id == selfies.id)
        )
        shown = (
            await db.scalars(
                select(PhotoSelectionCandidate.media_asset_id)
                .where(PhotoSelectionCandidate.selection_session_id == session.id)
                .order_by(PhotoSelectionCandidate.rank)
            )
        ).all()
        assert shown == [data["selfie"], data["mirror"]]
        # Girl photos never leak into library photos for other categories.
        assert (await db.get(MediaAsset, data["selfie"])).provider == "persona"


@pytest.mark.integration
async def test_preflight_checks_girl_accounts_and_photo_ownership(sessions, tmp_path):
    data = await persona_campaign(sessions, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
    ) as client:
        await persona_planner(sessions, tmp_path, client, data["embedder"]).plan(
            data["campaign"], MediaPlanInput()
        )
    settings = Settings(_env_file=None, media_storage_dir=tmp_path)
    async with sessions() as db:
        report, _ = await preflight(db, data["campaign"], None, None, data["storage"], settings)
    assert report["persona_targets"] == 2 and report["persona_photo_mismatch"] == 0
    assert report["persona_photos_incomplete"] == 0
    girl = next(a for a in report["assigned_per_account"] if a["persona"])
    # The jokes public got no photo: it is skipped, so the girl sends the two others.
    assert girl["account_id"] == str(data["girl"]) and girl["assigned"] == 2
    assert report["media_missing"] == 1
    async with sessions() as db, db.begin():
        stranger = Account(
            name="Чужая", vk_user_id=3, encrypted_access_token="mock", status=AccountStatus.active
        )
        db.add(stranger)
        await db.flush()
        other = await add_photo(db, stranger.id, "selfie", image_bytes(seed=3), data["storage"], [])
        row = await submission_for(db, data["campaign"], data["communities"]["selfies"])
        row.media_asset_id = other.media_asset_id
    async with sessions() as db:
        report, _ = await preflight(db, data["campaign"], None, None, data["storage"], settings)
    assert report["persona_photo_mismatch"] == 1 and report["ready"] is False
    # A girl with only one of her two photos cannot send her publics.
    async with sessions() as db, db.begin():
        await remove_photo(db, data["girl"], data["selfie"])
    async with sessions() as db:
        report, _ = await preflight(db, data["campaign"], None, None, data["storage"], settings)
    assert report["persona_photos_incomplete"] == 1 and report["ready"] is False
    async with sessions() as db, db.begin():
        for membership in await db.scalars(select(CampaignAccount)):
            membership.persona = False
    async with sessions() as db:
        report, _ = await preflight(db, data["campaign"], None, None, data["storage"], settings)
    assert report["persona_account_missing"] == 2 and report["ready"] is False


@pytest.mark.integration
async def test_girl_has_several_selfies_and_other_photos_of_her_own(
    client, sessions, tmp_path, monkeypatch
):
    from dropgrid.photos.domain import PhotoPolicy
    from dropgrid.photos.images import normalize_image

    client._transport.app.state.photo_engine.storage = LocalMediaStorage(tmp_path)
    async with sessions() as db, db.begin():
        girl = Account(name="Аня", vk_user_id=1, status=AccountStatus.active)
        other_girl = Account(name="Оля", vk_user_id=2, status=AccountStatus.active)
        library = MediaAsset(
            storage_key="library.jpg",
            provider="pixabay",
            sha256=normalize_image(image_bytes(seed=11), PhotoPolicy()).sha256,
        )
        db.add_all([girl, other_girl, library])
        await db.flush()
        gid, oid = girl.id, other_girl.id
    url = f"/api/v1/accounts/{gid}/persona-photos"

    async def add(kind, seed, account=gid, tags=()):
        return await client.post(
            f"/api/v1/accounts/{account}/persona-photos",
            params=[("kind", kind), *(("tags", t) for t in tags)],
            content=image_bytes(seed=seed),
            headers={"content-type": "image/jpeg"},
        )

    first = await add("selfie", 7, tags=["Лицо "])
    assert first.status_code == 201
    assert first.json()["kind"] == "selfie" and first.json()["tags"] == ["селфи", "лицо"]
    assert (await add("selfie", 8)).status_code == 201
    other = await add("other", 9, tags=["в зеркале"])
    assert other.json()["kind"] == "other" and other.json()["tags"] == ["в зеркале"]
    assert [p["kind"] for p in (await client.get(url)).json()] == ["selfie", "selfie", "other"]
    duplicate = await add("other", 7)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {"code": "persona_photo_duplicate"}
    # Her photo can never be attached to another account's token.
    taken = await add("selfie", 9, account=oid)
    assert taken.status_code == 409 and taken.json()["detail"] == {"code": "persona_photo_taken"}
    in_library = await add("selfie", 11, account=oid)
    assert in_library.json()["detail"] == {"code": "persona_photo_in_library"}
    # A selfie moved to «не селфи» loses its selfie tag; descriptions can change.
    moved = await client.patch(f"{url}/{first.json()['media_asset_id']}", json={"kind": "other"})
    assert moved.json()["kind"] == "other" and moved.json()["tags"] == ["лицо"]
    retagged = await client.patch(
        f"{url}/{other.json()['media_asset_id']}", json={"tags": ["Полный рост"]}
    )
    assert retagged.json()["tags"] == ["полный рост"]
    assert [p["kind"] for p in (await client.get(url)).json()] == ["selfie", "other", "other"]
    monkeypatch.setattr(persona_photos, "MAX_PHOTOS_PER_ACCOUNT", 3)
    limit = await add("selfie", 12)
    assert limit.status_code == 409 and limit.json()["detail"] == {"code": "persona_photo_limit"}
    monkeypatch.undo()
    assert (await client.post(url, params={"kind": "selfie"}, content=b"no")).status_code == 422
    small = await client.post(
        url, params={"kind": "other"}, content=image_bytes(seed=13, size=(300, 300))
    )
    assert small.status_code == 422
    assert (await add("profile", 14)).status_code == 422
    assert (await client.post(url, content=image_bytes(seed=15))).status_code == 422
    asset = other.json()["media_asset_id"]
    assert (await client.delete(f"{url}/{asset}")).status_code == 204
    assert (await client.delete(f"{url}/{asset}")).status_code == 404
    assert len((await client.get(url)).json()) == 2
    assert (await client.get(f"/api/v1/accounts/{uuid4()}/persona-photos")).status_code == 404


@pytest.mark.integration
async def test_sender_never_sends_a_girls_photo_with_another_token(sessions, tmp_path):
    from test_campaign_sender import mock, seed, start

    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    async with sessions() as db, db.begin():
        owner = Account(name="Аня", vk_user_id=7, encrypted_access_token="mock")
        db.add(owner)
        await db.flush()
        asset = await db.get(MediaAsset, data[2])
        asset.provider = "persona"
        db.add(AccountPersonaPhoto(account_id=owner.id, kind="selfie", media_asset_id=asset.id))
    sender, seen, _ = mock(sessions, data[4])
    await sender.tick(data[3][0])
    assert seen == []
    async with sessions() as db:
        row = await db.get(Submission, data[3][0])
        assert row.status == SubmissionStatus.skipped
        assert row.error_code == "persona_photo_mismatch"


@pytest.mark.integration
async def test_campaign_asks_for_girl_accounts(client, sessions, tmp_path):
    data = await persona_campaign(sessions, tmp_path)
    summary = (await client.get(f"/api/v1/campaigns/{data['campaign']}/persona")).json()
    assert summary["categories"] == [{"category": "ЗНАКОМСТВА", "count": 2}]
    assert summary["targets"] == 2 and summary["default_quota"] == 100
    assert summary["accounts"] == [
        {
            "account_id": str(data["girl"]),
            "name": "Аня",
            "quota": 100,
            "selfies": 1,
            "others": 1,
        }
    ]
    url = f"/api/v1/campaigns/{data['campaign']}/prepare-workflow"
    outside = await client.post(
        url, json={"account_ids": [str(data["boy"])], "persona_account_ids": [str(data["girl"])]}
    )
    assert outside.status_code == 409
    async with sessions() as db:
        flags = {m.account_id: m.persona for m in await db.scalars(select(CampaignAccount))}
    assert flags[data["girl"]] is True
    switched = await client.post(
        url,
        json={
            "account_ids": [str(data["boy"]), str(data["girl"])],
            "persona_account_ids": [str(data["boy"])],
        },
    )
    assert switched.status_code == 200
    async with sessions() as db:
        flags = {
            m.account_id: m.persona
            for m in await db.scalars(select(CampaignAccount).where(CampaignAccount.enabled))
        }
    assert flags == {data["boy"]: True, data["girl"]: False}


@pytest.mark.integration
async def test_girl_photo_choices_can_be_confirmed_in_review(sessions, tmp_path):
    from dropgrid.photos.review import choose, confirm, latest_selection

    data = await persona_campaign(sessions, tmp_path)
    async with sessions() as db, db.begin():
        campaign = await db.get(Campaign, data["campaign"])
        relation = await db.get(GridCommunity, (campaign.grid_id, data["communities"]["selfies"]))
        relation.content_hint = "селфи в зеркале"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No download"))
    ) as client:
        planner = persona_planner(sessions, tmp_path, client, data["embedder"])
        await planner.plan(data["campaign"], MediaPlanInput())
        async with sessions() as db, db.begin():
            selfies = await submission_for(db, data["campaign"], data["communities"]["selfies"])
            sid = selfies.id
            # One girl's photo is meant to go to many publics: far beyond any library limit.
            planner.settings = planner.settings.model_copy(update={"photo_max_reuse_per_asset": 3})
            for i in range(planner.settings.photo_max_reuse_per_asset + 1):
                other = Community(domain=f"more_dating_{i}", resolution_status="resolved")
                db.add(other)
                await db.flush()
                db.add(
                    Submission(
                        campaign_id=data["campaign"],
                        community_id=other.id,
                        media_asset_id=data["mirror"],
                    )
                )
            # The reviewer prefers the alternative: the mirror photo.
            selection = await latest_selection(db, sid)
            mirror_rank = next(
                c.rank
                for c in await db.scalars(
                    select(PhotoSelectionCandidate).where(
                        PhotoSelectionCandidate.selection_session_id == selection.id
                    )
                )
                if c.media_asset_id == data["mirror"]
            )
            await choose(db, sid, mirror_rank, shown_ranks=[1, 2])
        await confirm(planner, sid)
    async with sessions() as db:
        assert (await latest_selection(db, sid)).confirmed_at is not None
        assert (await db.get(Submission, sid)).media_asset_id == data["mirror"]
