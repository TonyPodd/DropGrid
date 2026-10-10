"""Real PostgreSQL leases plus mocked VK API and multipart boundaries."""

import asyncio
import hashlib
from datetime import timedelta
from io import BytesIO
from urllib.parse import parse_qs

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Campaign,
    Community,
    CommunityMediaUsage,
    Grid,
    GridCategoryGender,
    GridCommunity,
    MediaAsset,
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
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.photos import WallPhotoUploader
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.services.catalog import ConflictError
from dropgrid.services.sending import (
    CampaignSender,
    cancel_campaign,
    refresh_campaigns,
    start_campaign,
)

pytestmark = pytest.mark.integration
GROUP = 242100737
USER = 615459987


class NoWait:
    async def acquire(self, account_id):
        pass


class Tokens:
    async def get_token(self, account_id):
        return SecretStr("mock-secret-token")


class Crash(BaseException):
    pass


async def seed(sessions, tmp_path, count=1):
    output = BytesIO()
    Image.new("RGB", (800, 800), "blue").save(output, "JPEG")
    data = output.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    storage = LocalMediaStorage(tmp_path)
    key = f"{digest[:2]}/{digest}.jpg"
    path = storage.path(key)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    async with sessions() as s, s.begin():
        account = Account(
            name="Sender",
            vk_user_id=USER,
            encrypted_access_token="mock-only",
            gender_tag=GenderTag.male,
        )
        grid = Grid(name="Pilot")
        asset = MediaAsset(
            storage_key=key, sha256=digest, width=800, height=800, mime_type="image/jpeg"
        )
        s.add_all([account, grid, asset])
        await s.flush()
        campaign = Campaign(
            name="Pilot",
            grid_id=grid.id,
            track_url="https://vk.com/audio2000410139_456245636",
            track_owner_id=2000410139,
            track_audio_id=456245636,
            status=CampaignStatus.ready,
            caption="test caption",
        )
        s.add(campaign)
        s.add(GridCategoryGender(grid_id=grid.id, category="ОБЩЕЕ", gender=CategoryGender.unisex))
        await s.flush()
        ids = []
        for i in range(count):
            community = Community(
                domain=f"target{i}", vk_group_id=GROUP + i, resolution_status="resolved"
            )
            s.add(community)
            await s.flush()
            s.add(GridCommunity(grid_id=grid.id, community_id=community.id, category="ОБЩЕЕ"))
            row = Submission(
                campaign_id=campaign.id, community_id=community.id, media_asset_id=asset.id
            )
            s.add(row)
            await s.flush()
            ids.append(row.id)
        return campaign.id, account.id, asset.id, ids, storage


async def start(sessions, seed, settings=None, limit=None):
    cid, aid, _, _, storage = seed
    async with sessions() as s, s.begin():
        return await start_campaign(
            s, cid, aid, limit, storage, settings or Settings(_env_file=None)
        )


async def get(sessions, id, model=Submission):
    async with sessions() as s:
        return await s.get(model, id)


async def due(sessions, id):
    async with sessions() as s, s.begin():
        row = await s.get(Submission, id)
        row.vk_send_lease_until = utcnow() - timedelta(seconds=1)
        row.vk_send_next_at = None
        account = await s.get(Account, row.account_id)
        account.vk_next_send_at = None


def mock(
    sessions,
    storage,
    *,
    error=None,
    empty=0,
    hook=None,
    writes=True,
    allowlist=frozenset(),
    user_owned_upload=False,
):
    seen = []
    photo = {"owner_id": -GROUP, "id": 100}
    post = {
        "id": 42,
        "owner_id": -GROUP,
        "from_id": USER,
        "post_type": "suggest",
        "date": int(utcnow().timestamp()),
        "attachments": [
            {"type": "photo", "photo": photo.copy()},
            {"type": "audio", "audio": {"owner_id": 2000410139, "id": 456245636}},
        ],
    }
    reads = 0

    async def handle(request):
        nonlocal reads
        method = request.url.path.rsplit("/", 1)[-1]
        params = parse_qs(request.content.decode())
        seen.append((method, params))
        if hook:
            await hook(method, params)
        if method == error:
            raise httpx.ReadTimeout("secret upstream URL", request=request)
        if method == "photos.getWallUploadServer":
            photo["owner_id"] = USER if user_owned_upload else -int(params["group_id"][0])
            photo["id"] += 1
            result = {
                "upload_url": "https://pu.vk.com/upload?private=test",
                "album_id": 1,
                "user_id": USER,
            }
        elif method == "photos.saveWallPhoto":
            result = [photo.copy()]
        elif method == "wall.post":
            assert params["attachments"] == [
                f"photo{photo['owner_id']}_{photo['id']},audio2000410139_456245636"
            ]
            assert params["message"] == ["test caption"]
            assert "guid" in params and params["from_group"] == ["0"]
            post["owner_id"] = int(params["owner_id"][0])
            post["attachments"][0]["photo"] = (
                {"owner_id": int(params["owner_id"][0]), "id": photo["id"] + 1000}
                if user_owned_upload
                else photo.copy()
            )
            result = {"post_id": 42}
        elif method == "wall.getById":
            reads += 1
            result = {"items": [] if reads <= empty else [post]}
        else:
            pytest.fail("Unexpected VK method")
        return httpx.Response(200, json={"response": result})

    async def upload(request):
        seen.append(("multipart", {}))
        assert "multipart/form-data" in request.headers["content-type"]
        assert b"mock-secret-token" not in request.content
        return httpx.Response(200, json={"server": 9, "photo": "uploaded", "hash": "private-hash"})

    client = VKClient(
        Settings(
            _env_file=None,
            vk_write_enabled=writes,
            vk_test_allowed_community_ids=allowlist,
            vk_max_attempts=1,
        ),
        transport=httpx.MockTransport(handle),
        limiter=NoWait(),
    )
    uploader = WallPhotoUploader(client, transport=httpx.MockTransport(upload))
    return CampaignSender(sessions, client, Tokens(), storage, uploader), seen, post


async def test_success_and_repeated_ticks_are_idempotent(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    cid, aid, mid, ids, storage = data
    sender, seen, _ = mock(sessions, storage)
    assert await sender.tick(ids[0])
    for _ in range(3):
        assert not await sender.tick(ids[0])
    assert [m for m, _ in seen] == [
        "photos.getWallUploadServer",
        "multipart",
        "photos.saveWallPhoto",
        "wall.post",
        "wall.getById",
    ]
    row = await get(sessions, ids[0])
    assert row.status == SubmissionStatus.submitted and row.account_id == aid
    assert row.vk_send_phase == "verified" and row.vk_send_guid == row.id
    assert row.vk_send_receipt_post_id == row.vk_suggested_post_id == 42
    assert row.vk_canonical_photo_owner_id == -GROUP and row.vk_canonical_photo_id == 101
    assert (row.vk_audio_owner_id, row.vk_audio_id) == (2000410139, 456245636)
    assert row.submitted_at and row.vk_send_lease_token is None
    assert (await get(sessions, mid, MediaAsset)).usage_count == 1
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(CommunityMediaUsage)) == 1
    assert (await get(sessions, cid, Campaign)).status == CampaignStatus.monitoring


@pytest.mark.parametrize(
    "phase",
    ["claimed", "photo_uploaded", "wall_post_started", "receipt_received", "readback_pending"],
)
async def test_crash_phase_recovery(sessions, tmp_path, phase):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sid = data[3][0]
    async with sessions() as s, s.begin():
        row = await s.get(Submission, sid)
        row.status = SubmissionStatus.sending
        row.vk_send_phase = phase
        row.vk_photo_upload_owner_id, row.vk_photo_upload_id = -GROUP, 100
        if phase in {"receipt_received", "readback_pending"}:
            row.vk_send_receipt_post_id = 42
    sender, seen, _ = mock(sessions, data[4])
    await sender.tick(sid)
    methods = [m for m, _ in seen]
    row = await get(sessions, sid)
    if phase == "wall_post_started":
        assert methods == [] and row.error_code == "wall_post_outcome_unknown"
        await due(sessions, sid)
        await sender.tick(sid)
        assert seen == []
    elif phase in {"receipt_received", "readback_pending"}:
        assert methods == ["wall.getById"] and row.status == SubmissionStatus.submitted
    else:
        assert methods.count("wall.post") == 1 and methods.count("multipart") == 1
    assert row.vk_send_guid == sid


@pytest.mark.parametrize(
    "error", ["wall.post", "photos.getWallUploadServer", "photos.saveWallPhoto"]
)
async def test_transport_failure_write_boundary(sessions, tmp_path, error):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sid = data[3][0]
    sender, seen, _ = mock(sessions, data[4], error=error)
    for _ in range(5):
        await sender.tick(sid)
        await due(sessions, sid)
    row = await get(sessions, sid)
    calls = [m for m, _ in seen]
    if error == "wall.post":
        assert calls.count("wall.post") == 1
        assert (
            row.error_code == "wall_post_outcome_unknown" and row.status == SubmissionStatus.sending
        )
    else:
        assert calls.count(error) == 3 and "wall.post" not in calls
        assert row.status == SubmissionStatus.failed
    assert row.vk_send_guid == sid


async def test_receipt_persisted_before_delayed_readback_and_no_resend(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sid = data[3][0]

    async def hook(method, params):
        if method == "wall.getById":
            row = await get(sessions, sid)
            assert row.vk_send_receipt_post_id == 42
            assert row.vk_send_phase in {"receipt_received", "readback_pending"}

    sender, seen, _ = mock(sessions, data[4], empty=2, hook=hook)
    for _ in range(3):
        await sender.tick(sid)
        await due(sessions, sid)
    assert [m for m, _ in seen].count("wall.post") == 1
    assert [m for m, _ in seen].count("wall.getById") == 3
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted
    assert (await get(sessions, data[2], MediaAsset)).usage_count == 1


async def test_receipt_survives_actual_process_crash(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sid = data[3][0]

    async def crash(method, params):
        if method == "wall.getById":
            raise Crash

    sender, seen, _ = mock(sessions, data[4], hook=crash)
    with pytest.raises(Crash):
        await sender.tick(sid)
    assert (await get(sessions, sid)).vk_send_receipt_post_id == 42
    await due(sessions, sid)
    recovery, recovered, post = mock(sessions, data[4])
    post["attachments"][0]["photo"]["id"] = 101
    await recovery.tick(sid)
    assert [m for m, _ in recovered] == ["wall.getById"]
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted


async def test_crash_immediately_before_wall_post_never_replays(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sid = data[3][0]

    async def crash(method, params):
        if method == "wall.post":
            assert (await get(sessions, sid)).vk_send_phase == "wall_post_started"
            raise Crash

    sender, seen, _ = mock(sessions, data[4], hook=crash)
    with pytest.raises(Crash):
        await sender.tick(sid)
    await due(sessions, sid)
    await sender.tick(sid)
    assert [m for m, _ in seen].count("wall.post") == 1
    assert (await get(sessions, sid)).error_code == "wall_post_outcome_unknown"


async def test_concurrent_workers_and_live_expired_lease_do_not_overlap(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sid = data[3][0]
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(method, params):
        if method == "photos.getWallUploadServer":
            entered.set()
            await release.wait()

    first, seen, _ = mock(sessions, data[4], hook=pause)
    second, seen2, _ = mock(sessions, data[4])
    task = asyncio.create_task(first.tick(sid))
    await asyncio.wait_for(entered.wait(), 5)
    await due(sessions, sid)
    assert not await second.tick(sid)
    release.set()
    await task
    assert seen2 == [] and "wall.post" not in [m for m, _ in seen]
    # Lease fenced out after upload. Recovery safely uploads again with same GUID.
    assert await second.tick(sid)
    assert [m for m, _ in seen2].count("wall.post") == 1


async def test_same_asset_is_freshly_uploaded_per_submission_and_paced(sessions, tmp_path):
    data = await seed(sessions, tmp_path, count=2)
    await start(sessions, data)
    sender, seen, _ = mock(sessions, data[4])
    await sender.tick(data[3][0])
    assert not await sender.tick(data[3][1])
    await due(sessions, data[3][1])
    await sender.tick(data[3][1])
    assert [m for m, _ in seen].count("wall.post") == 2
    assert [m for m, _ in seen].count("multipart") == 2
    assert (await get(sessions, data[2], MediaAsset)).usage_count == 2
    assert (await get(sessions, data[3][0])).vk_canonical_photo_id != (
        await get(sessions, data[3][1])
    ).vk_canonical_photo_id


@pytest.mark.parametrize(
    "problem",
    [
        "unresolved",
        "unavailable",
        "gender",
        "missing_media",
        "disabled_media",
        "missing_file",
        "inactive_account",
        "missing_token",
        "wrong_lifecycle",
        "audio",
    ],
)
async def test_strict_start_preconditions(sessions, tmp_path, problem):
    data = await seed(sessions, tmp_path, count=2)
    async with sessions() as s, s.begin():
        row = await s.get(Submission, data[3][0])
        community = await s.get(Community, row.community_id)
        asset = await s.get(MediaAsset, data[2])
        account = await s.get(Account, data[1])
        campaign = await s.get(Campaign, data[0])
        if problem == "unresolved":
            community.resolution_status = "unresolved"
        if problem == "unavailable":
            community.is_active = False
        if problem == "gender":
            community.required_gender_tag = GenderTag.female
        if problem == "missing_media":
            row.media_asset_id = None
        if problem == "disabled_media":
            asset.enabled = False
        if problem == "missing_file":
            data[4].path(asset.storage_key).unlink()
        if problem == "inactive_account":
            account.status = AccountStatus.disabled
        if problem == "missing_token":
            account.encrypted_access_token = None
        if problem == "wrong_lifecycle":
            campaign.status = CampaignStatus.draft
        if problem == "audio":
            campaign.track_audio_id = None
    if problem in {"unresolved", "unavailable", "gender"}:
        await start(sessions, data)
        row = await get(sessions, data[3][0])
        assert row.status == SubmissionStatus.skipped
        assert row.error_code in {"community_unavailable", "account_gender_mismatch"}
        sender, seen, _ = mock(sessions, data[4])
        await sender.tick(row.id)
        assert seen == []
    else:
        with pytest.raises(ConflictError):
            await start(sessions, data)
        assert (await get(sessions, data[0], Campaign)).status != CampaignStatus.running


@pytest.mark.parametrize(
    "problem",
    [
        "cancelled",
        "write_disabled",
        "inactive",
        "token",
        "missing_file",
        "disabled_media",
        "outside_allowlist",
    ],
)
async def test_runtime_preconditions_do_not_write(sessions, tmp_path, problem):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    async with sessions() as s, s.begin():
        if problem == "cancelled":
            await cancel_campaign(s, data[0])
        if problem == "inactive":
            (await s.get(Account, data[1])).status = AccountStatus.disabled
        if problem == "token":
            (await s.get(Account, data[1])).encrypted_access_token = None
        asset = await s.get(MediaAsset, data[2])
        if problem == "disabled_media":
            asset.enabled = False
        if problem == "missing_file":
            data[4].path(asset.storage_key).unlink()
    sender, seen, _ = mock(
        sessions,
        data[4],
        writes=problem != "write_disabled",
        allowlist=frozenset({999}) if problem == "outside_allowlist" else frozenset(),
    )
    await sender.tick(data[3][0])
    assert seen == []


async def test_pilot_scope_and_lifecycle_and_cancellation(sessions, tmp_path):
    data = await seed(sessions, tmp_path, count=3)
    await start(sessions, data, limit=1)
    assert (await get(sessions, data[3][1])).error_code == "pilot_scope_excluded"
    sender, _, _ = mock(sessions, data[4])
    await sender.tick(data[3][0])
    assert (await get(sessions, data[0], Campaign)).status == CampaignStatus.monitoring
    async with sessions() as s, s.begin():
        (await s.get(Submission, data[3][0])).status = SubmissionStatus.published
    await refresh_campaigns(sessions)
    assert (await get(sessions, data[0], Campaign)).status == CampaignStatus.completed
    async with sessions() as s, s.begin():
        with pytest.raises(ConflictError):
            await cancel_campaign(s, data[0])


async def test_cancel_during_upload_prevents_wall_post(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)

    async def cancel(method, params):
        if method == "photos.saveWallPhoto":
            async with sessions() as s, s.begin():
                await cancel_campaign(s, data[0])

    sender, seen, _ = mock(sessions, data[4], hook=cancel)
    await sender.tick(data[3][0])
    assert "wall.post" not in [m for m, _ in seen]
    assert (await get(sessions, data[0], Campaign)).status == CampaignStatus.cancelled


async def test_start_api_preflight_cancel_never_call_vk(client, sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    # Override local storage directory, never credentials.
    client._transport.app.state.vk_client.settings.media_storage_dir = tmp_path
    cid, aid = data[:2]
    report = await client.post(
        f"/api/v1/campaigns/{cid}/preflight", json={"account_id": str(aid), "max_submissions": 1}
    )
    assert report.status_code == 200 and report.json()["ready"]
    response = await client.post(
        f"/api/v1/campaigns/{cid}/start", json={"account_id": str(aid), "max_submissions": 1}
    )
    assert response.status_code == 200 and response.json()["account_id"] == str(aid)
    assert "mock-only" not in response.text
    assert (
        await client.post(f"/api/v1/campaigns/{cid}/start", json={"account_id": str(aid)})
    ).status_code == 409
    assert (await client.post(f"/api/v1/campaigns/{cid}/cancel")).json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "mode",
    [
        "protocol",
        "rejected",
        "readback_exhausted",
        "wrong_author",
        "wrong_audio",
        "wrong_photo",
        "published",
    ],
)
async def test_no_resend_on_rejection_or_unverifiable_readback(sessions, tmp_path, mode):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sid = data[3][0]
    sender, seen, post = mock(sessions, data[4], empty=100 if mode == "readback_exhausted" else 0)
    original = sender.client.create_wall_post
    from dropgrid.integrations.vk.errors import VKPermissionError, VKProtocolError

    async def boundary(params, **kwargs):
        if mode == "rejected":
            seen.append(("wall.post", {}))
            raise VKPermissionError("wall.post", "Permission denied", 15, 1134)
        receipt = await original(params, **kwargs)
        if mode == "protocol":
            raise VKProtocolError("wall.post", "Unparseable successful response")
        if mode == "wrong_author":
            post["from_id"] = 123
        if mode == "wrong_audio":
            post["attachments"][1]["audio"]["id"] = 999
        if mode == "wrong_photo":
            post["attachments"][0]["photo"]["owner_id"] = USER
        if mode == "published":
            post["post_type"] = "post"
        return receipt

    sender.client.create_wall_post = boundary
    for _ in range(10):
        await sender.tick(sid)
        await due(sessions, sid)
    calls = [m for m, _ in seen]
    assert calls.count("wall.post") == 1
    row = await get(sessions, sid)
    if mode == "rejected":
        assert row.status == SubmissionStatus.failed and row.error_code == "vk_rejected_15"
        assert (await get(sessions, data[0], Campaign)).status == CampaignStatus.failed
    elif mode == "protocol":
        assert row.error_code == "wall_post_outcome_unknown"
    else:
        assert (
            row.vk_send_receipt_post_id == 42 and row.error_code == "suggestion_readback_exhausted"
        )
        assert calls.count("wall.getById") == 6
    assert (await get(sessions, data[2], MediaAsset)).usage_count == 0


async def test_success_then_crash_before_receipt_persistence_is_quarantined(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sender, seen, _ = mock(sessions, data[4])
    original = sender._save

    async def interrupted(claim, **changes):
        if changes.get("vk_send_phase") == "receipt_received":
            raise Crash
        return await original(claim, **changes)

    sender._save = interrupted
    sid = data[3][0]
    with pytest.raises(Crash):
        await sender.tick(sid)
    await due(sessions, sid)
    await sender.tick(sid)
    row = await get(sessions, sid)
    assert row.vk_send_receipt_post_id is None and row.error_code == "wall_post_outcome_unknown"
    assert [m for m, _ in seen].count("wall.post") == 1


async def test_readback_recovery_after_cancel_with_write_gate_off(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sender, seen, _ = mock(sessions, data[4], empty=1)
    sid = data[3][0]
    await sender.tick(sid)
    async with sessions() as s, s.begin():
        await cancel_campaign(s, data[0])
    sender.client.settings.vk_write_enabled = False
    await due(sessions, sid)
    await sender.tick(sid)
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted
    assert [m for m, _ in seen].count("wall.post") == 1
    assert (await get(sessions, data[0], Campaign)).status == CampaignStatus.cancelled


async def test_stale_lease_cannot_commit_receipt_or_write_boundary(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sender, seen, _ = mock(sessions, data[4])
    claim = await sender._claim(data[3][0])
    await due(sessions, claim.id)
    replacement = await sender._claim(claim.id)
    assert replacement.vk_send_guid == claim.vk_send_guid
    assert replacement.vk_send_lease_token != claim.vk_send_lease_token
    assert not await sender._save(claim, vk_send_receipt_post_id=123)
    assert not await sender._wall_boundary(claim)
    assert seen == []


async def test_not_found_is_terminal_completed(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    async with sessions() as s, s.begin():
        (await s.get(Submission, data[3][0])).status = SubmissionStatus.not_found
    await refresh_campaigns(sessions)
    assert (await get(sessions, data[0], Campaign)).status == CampaignStatus.completed


async def test_vk_copies_user_owned_upload_to_canonical_group_photo(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    await start(sessions, data)
    sender, seen, _ = mock(sessions, data[4], user_owned_upload=True)
    await sender.tick(data[3][0])
    row = await get(sessions, data[3][0])
    assert row.status == SubmissionStatus.submitted
    assert (row.vk_photo_upload_owner_id, row.vk_photo_upload_id) == (USER, 101)
    assert (row.vk_canonical_photo_owner_id, row.vk_canonical_photo_id) == (-GROUP, 1101)
    assert row.vk_send_receipt_post_id == row.vk_suggested_post_id == 42
    assert [method for method, _ in seen].count("wall.post") == 1
    assert (await get(sessions, data[2], MediaAsset)).usage_count == 1
