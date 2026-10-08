import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import update

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Campaign,
    Community,
    Grid,
    GridCommunity,
    MediaAsset,
    Submission,
    VKNotificationCursor,
)
from dropgrid.domain.enums import SubmissionStatus
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.models import VKNotification, WallPostDetails, WallPostReceipt
from dropgrid.services.catalog import ConflictError
from dropgrid.services.publication import (
    PublicationMonitor,
    notification_mapping,
    record_suggested_submission,
)

NOW = datetime.fromtimestamp(1791484000, UTC)
SUGGESTION = {
    "id": 4,
    "owner_id": -242100737,
    "from_id": 615459987,
    "post_type": "suggest",
    "date": 1791481029,
    "text": "[marker]",
    "attachments": [
        {"type": "photo", "photo": {"owner_id": -242100737, "id": 456239018}},
        {"type": "audio", "audio": {"owner_id": 2000410139, "id": 456245636}},
    ],
}
PUBLISHED = {**SUGGESTION, "id": 5, "post_type": "post", "from_id": -242100737, "date": 1791483366}
EVENT = {
    "type": "wall_publish",
    "date": 1791483366,
    "feedback": {
        **PUBLISHED,
        "to_id": -242100737,
        "attachments": [
            {"type": "photo", "photo": {"owner_id": -242100737, "id": 456239018, "post_id": 4}},
            {"type": "audio", "audio": {"owner_id": 2000410139, "id": 456245636}},
        ],
    },
}


class Tokens:
    async def get_token(self, account_id):
        return SecretStr("mock-token")


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("dropgrid.services.publication.utcnow", lambda: NOW)


async def seed(sessions, expired=False):
    async with sessions() as s, s.begin():
        account = Account(name="Test", vk_user_id=615459987, encrypted_access_token="test-only")
        grid = Grid(name="Grid")
        community = Community(domain="club242100737", vk_group_id=242100737)
        media = MediaAsset(storage_key="test.jpg")
        s.add_all([account, grid, community, media])
        await s.flush()
        campaign = Campaign(
            name="Campaign",
            grid_id=grid.id,
            track_url="https://vk.com/audio1_2",
            publication_check_hours=72,
        )
        s.add(campaign)
        await s.flush()
        s.add(GridCommunity(grid_id=grid.id, community_id=community.id, category="TEST"))
        row = Submission(
            campaign_id=campaign.id, community_id=community.id, media_asset_id=media.id
        )
        s.add(row)
        await s.flush()
        await record_suggested_submission(
            s,
            row,
            account_id=account.id,
            receipt=WallPostReceipt(post_id=4),
            suggestion=WallPostDetails(**SUGGESTION),
        )
        if expired:
            row.vk_suggested_at = NOW - timedelta(hours=73)
        return row.id, account.id, campaign.id, media.id


async def get(sessions, id):
    async with sessions() as s:
        return await s.get(Submission, id)


def mock_client(
    notifications=None,
    published=None,
    old=None,
    wall=None,
    error_method=None,
    error_code=6,
    next_from=None,
    on_request=None,
):
    seen = []
    events = [EVENT] if notifications is None else notifications
    published = PUBLISHED if published is None else published
    wall = [PUBLISHED] if wall is None else wall

    async def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        params = parse_qs(request.content.decode())
        seen.append((method, params))
        assert method in ("notifications.get", "wall.getById", "wall.get")
        if on_request:
            await on_request(method, params)
        if method == error_method:
            return httpx.Response(200, json={"error": {"error_code": error_code}})
        if method == "notifications.get":
            assert params["filters"] == ["wall"]
            response = {"count": len(events), "items": events}
            if next_from:
                response["next_from"] = next_from
        elif method == "wall.get":
            response = {"count": len(wall), "items": wall}
        else:
            assert params["posts"][0].startswith("-242100737_")
            value = old if int(params["posts"][0].rsplit("_", 1)[-1]) == 4 else published
            response = {"items": [value] if value else []}
        return httpx.Response(200, json={"response": response})

    return VKClient(
        Settings(_env_file=None, vk_max_attempts=1, vk_min_interval_seconds=0.34),
        transport=httpx.MockTransport(handle),
    ), seen


@pytest.mark.integration
async def test_observed_lifecycle_and_idempotency(sessions):
    sid, aid, cid, mid = await seed(sessions)
    async with mock_client()[0] as client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        assert await monitor.poll_account(aid)
        row = await get(sessions, sid)
        assert row.status == SubmissionStatus.published
        assert row.vk_suggested_post_id == 4 and row.vk_published_post_id == 5
        assert row.published_post_url == "https://vk.com/wall-242100737_5"
        assert row.published_at == datetime.fromtimestamp(1791483366, UTC)
        assert await monitor.process_notification(aid, VKNotification(**EVENT))
        async with sessions() as s, s.begin():
            assert (await s.get(MediaAsset, mid)).usage_count == 1
            assert (await s.get(VKNotificationCursor, aid)).last_polled_at == NOW
            await record_suggested_submission(
                s,
                await s.get(Submission, sid),
                account_id=aid,
                receipt=WallPostReceipt(post_id=4),
                suggestion=WallPostDetails(**SUGGESTION),
            )
            assert (await s.get(MediaAsset, mid)).usage_count == 1
        assert (await monitor.check_submission(sid))["current_status"] == SubmissionStatus.published


@pytest.mark.integration
async def test_receipt_requires_group_owned_readback(sessions):
    sid, aid, cid, mid = await seed(sessions)
    wrong = deepcopy(SUGGESTION)
    wrong["attachments"][0]["photo"] = {"owner_id": 615459987, "id": 457239318}
    async with sessions() as s, s.begin():
        row = await s.get(Submission, sid)
        assert row.vk_canonical_photo_owner_id == -242100737
        with pytest.raises(ConflictError):
            await record_suggested_submission(
                s,
                row,
                account_id=aid,
                receipt=WallPostReceipt(post_id=4),
                suggestion=WallPostDetails(**wrong),
            )


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "no_photo",
        "no_old_id",
        "wrong_community",
        "wrong_suggestion",
        "wrong_photo",
        "wrong_account",
    ],
)
@pytest.mark.integration
async def test_nonmatching_events(sessions, mutation):
    sid, aid, *_ = await seed(sessions)
    event = deepcopy(EVENT)
    if mutation == "unknown":
        event["type"] = "future_type"
    if mutation == "no_photo":
        event["feedback"]["attachments"] = []
    if mutation == "no_old_id":
        del event["feedback"]["attachments"][0]["photo"]["post_id"]
    if mutation == "wrong_community":
        event["feedback"]["to_id"] = -123
    if mutation == "wrong_suggestion":
        event["feedback"]["attachments"][0]["photo"]["post_id"] = 42
    if mutation == "wrong_photo":
        event["feedback"]["attachments"][0]["photo"]["id"] = 99
    if mutation == "wrong_account":
        aid = uuid4()
    client, seen = mock_client()
    async with client:
        await PublicationMonitor(sessions, client, Tokens()).process_notification(
            aid, VKNotification(**event)
        )
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted
    assert not seen


@pytest.mark.parametrize(
    "mutation", ["absent", "suggest", "wrong_photo", "wrong_owner", "wrong_date"]
)
@pytest.mark.integration
async def test_notification_requires_confirmation(sessions, mutation):
    sid, aid, *_ = await seed(sessions)
    post = deepcopy(PUBLISHED)
    if mutation == "absent":
        post = {}
    if mutation == "suggest":
        post["post_type"] = "suggest"
    if mutation == "wrong_photo":
        post["attachments"][0]["photo"]["id"] = 99
    if mutation == "wrong_owner":
        post["owner_id"] = -123
    if mutation == "wrong_date":
        post["date"] = 1
    async with mock_client(published=post)[0] as client:
        assert not await PublicationMonitor(sessions, client, Tokens()).poll_account(aid)
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted
    async with sessions() as s:
        assert (await s.get(VKNotificationCursor, aid)).last_polled_at is None


@pytest.mark.integration
async def test_moderator_changes_allowed(sessions):
    sid, aid, *_ = await seed(sessions)
    post = deepcopy(PUBLISHED)
    post["text"] = "edited"
    post["from_id"] = 123
    post["attachments"] = post["attachments"][:1]
    async with mock_client(published=post)[0] as client:
        assert await PublicationMonitor(sessions, client, Tokens()).poll_account(aid)
    row = await get(sessions, sid)
    assert (
        row.status == SubmissionStatus.published and row.vk_monitor_evidence["audio_match"] is False
    )


@pytest.mark.integration
async def test_duplicate_receipt_is_ambiguous(sessions):
    sid, aid, cid, mid = await seed(sessions)
    async with sessions() as s, s.begin():
        original = await s.get(Submission, sid)
        campaign = Campaign(
            name="Other",
            grid_id=(await s.get(Campaign, cid)).grid_id,
            track_url="https://vk.com/audio1_2",
        )
        s.add(campaign)
        await s.flush()
        duplicate = Submission(
            campaign_id=campaign.id,
            community_id=original.community_id,
            account_id=aid,
            status=SubmissionStatus.submitted,
            vk_suggested_post_id=4,
            vk_suggested_at=original.vk_suggested_at,
            vk_canonical_photo_owner_id=-242100737,
            vk_canonical_photo_id=456239018,
        )
        s.add(duplicate)
        await s.flush()
        did = duplicate.id
    client, seen = mock_client()
    async with client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        assert not await monitor.poll_account(aid)
        await monitor.reconcile(sid, notifications_complete=True)
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted
    assert (await get(sessions, did)).status == SubmissionStatus.submitted
    assert not any(m == "wall.getById" for m, _ in seen)


@pytest.mark.integration
async def test_published_conflict(sessions):
    sid, aid, *_ = await seed(sessions)
    async with mock_client()[0] as client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        assert await monitor.process_notification(aid, VKNotification(**EVENT))
        other = deepcopy(EVENT)
        other["feedback"]["id"] = 6
        assert not await monitor.process_notification(aid, VKNotification(**other))
    row = await get(sessions, sid)
    assert (
        row.vk_published_post_id == 5
        and row.vk_monitor_evidence["result"] == "published_id_conflict"
    )


@pytest.mark.parametrize(
    "wall,expected",
    [
        ([], "submitted"),
        ([PUBLISHED], "published"),
        ([PUBLISHED, {**PUBLISHED, "id": 6}], "submitted"),
    ],
)
@pytest.mark.integration
async def test_fallback_uniqueness(sessions, wall, expected):
    sid, aid, *_ = await seed(sessions)
    async with mock_client(wall=wall)[0] as client:
        await PublicationMonitor(sessions, client, Tokens()).reconcile(
            sid, notifications_complete=True
        )
    assert (await get(sessions, sid)).status.value == expected


@pytest.mark.integration
async def test_pending_after_deadline(sessions):
    sid, aid, *_ = await seed(sessions, expired=True)
    client, seen = mock_client(old=SUGGESTION)
    async with client:
        await PublicationMonitor(sessions, client, Tokens()).reconcile(
            sid, notifications_complete=True
        )
    row = await get(sessions, sid)
    assert (
        row.status == SubmissionStatus.submitted
        and row.vk_monitor_evidence["pending_after_deadline"] is True
    )
    assert len(seen) == 1


@pytest.mark.parametrize("complete,expected", [(False, "submitted"), (True, "not_found")])
@pytest.mark.integration
async def test_deadline(sessions, complete, expected):
    sid, aid, *_ = await seed(sessions, expired=True)
    async with mock_client(wall=[])[0] as client:
        await PublicationMonitor(sessions, client, Tokens()).reconcile(
            sid, notifications_complete=complete
        )
    assert (await get(sessions, sid)).status.value == expected


@pytest.mark.parametrize(
    "method,code",
    [("notifications.get", 6), ("notifications.get", 10), ("wall.get", 6), ("wall.getById", 10)],
)
@pytest.mark.integration
async def test_temporary_failures(sessions, method, code):
    sid, aid, *_ = await seed(sessions, expired=True)
    async with mock_client(error_method=method, error_code=code, wall=[])[0] as client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        complete = await monitor.poll_account(aid) if method == "notifications.get" else True
        await monitor.reconcile(sid, notifications_complete=complete)
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted


@pytest.mark.integration
async def test_bounded_cursor(sessions):
    sid, aid, *_ = await seed(sessions)
    client, seen = mock_client(notifications=[], next_from="repeat")
    async with client:
        assert not await PublicationMonitor(sessions, client, Tokens()).poll_account(aid)
    assert len(seen) == 2
    async with sessions() as s:
        assert (await s.get(VKNotificationCursor, aid)).last_polled_at is None


@pytest.mark.integration
async def test_overlap_and_same_second(sessions):
    sid, aid, *_ = await seed(sessions)
    client, seen = mock_client(notifications=[{"type": "future", "date": 1791483366}, EVENT])
    async with client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        assert await monitor.poll_account(aid)
        assert await monitor.poll_account(aid, force=True)
    requests = [p for m, p in seen if m == "notifications.get"]
    assert int(requests[1]["start_time"][0]) == int(NOW.timestamp()) - 300
    assert (await get(sessions, sid)).status == SubmissionStatus.published


@pytest.mark.integration
async def test_concurrency_and_lease_fencing(sessions):
    sid, aid, *_ = await seed(sessions)
    started, release = asyncio.Event(), asyncio.Event()

    async def block(method, params):
        if method == "wall.getById":
            started.set()
            await release.wait()

    client, seen = mock_client(old=SUGGESTION, on_request=block)
    async with client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        first = asyncio.create_task(monitor.reconcile(sid, notifications_complete=True))
        await started.wait()
        await monitor.reconcile(sid, notifications_complete=True)
        assert len(seen) == 1
        release.set()
        await first
        target = await monitor._claim(sid, NOW)
        async with sessions() as s, s.begin():
            await s.execute(
                update(Submission)
                .where(Submission.id == sid)
                .values(vk_monitor_lease_until=NOW - timedelta(seconds=1))
            )
        assert not await monitor._finish(
            target, {"result": "stale"}, post=WallPostDetails(**PUBLISHED)
        )
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted


@pytest.mark.integration
async def test_api(client, sessions):
    from dropgrid.api.dependencies import token_provider, vk_client

    sid, aid, cid, *_ = await seed(sessions)
    vk, seen = mock_client()
    app = client._transport.app
    app.dependency_overrides[token_provider] = lambda: Tokens()
    app.dependency_overrides[vk_client] = lambda: vk
    async with vk:
        response = await client.post(f"/api/v1/submissions/{sid}/check-publication")
    assert response.status_code == 200 and response.json()["current_status"] == "published"
    response = await client.get(f"/api/v1/campaigns/{cid}/published")
    assert response.status_code == 200 and response.json()[0]["category"] == "TEST"
    assert response.json()[0]["published_post_url"] == "https://vk.com/wall-242100737_5"
    assert "mock-token" not in response.text and "test-only" not in response.text
    response = await client.get(f"/api/v1/campaigns/{cid}/submissions")
    assert response.json()["items"][0]["published_at"] is not None


def test_unknown_mapping():
    assert notification_mapping(VKNotification(type="future", parent={}, reply={})) == []
    mapping = notification_mapping(VKNotification(**EVENT))[0]
    assert (mapping.suggested_id, mapping.published_id) == (4, 5)


@pytest.mark.integration
async def test_account_poll_lease_and_no_db_lock_during_http(sessions):
    sid, aid, *_ = await seed(sessions)
    entered, release = asyncio.Event(), asyncio.Event()

    async def block(method, params):
        if method == "notifications.get":
            entered.set()
            await release.wait()
        # The monitor never holds the submission row transaction during VK HTTP.
        async with sessions() as s, s.begin():
            from sqlalchemy import select

            row = await s.scalar(
                select(Submission).where(Submission.id == sid).with_for_update(nowait=True)
            )
            assert row is not None

    client, seen = mock_client(on_request=block)
    async with client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        first = asyncio.create_task(monitor.poll_account(aid))
        await entered.wait()
        assert not await monitor.poll_account(aid)
        assert len(seen) == 1
        release.set()
        assert await first


@pytest.mark.integration
async def test_incomplete_wall_scan_and_legacy_receipts_remain_submitted(sessions):
    sid, aid, *_ = await seed(sessions, expired=True)
    seen = []

    def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        seen.append(method)
        if method == "wall.getById":
            response = {"items": []}
        else:
            # Newer posts: the lower time boundary has not been reached in 3 pages.
            response = {
                "count": 1000,
                "items": [{**PUBLISHED, "id": 99, "attachments": [], "date": int(NOW.timestamp())}],
            }
        return httpx.Response(200, json={"response": response})

    async with VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handle)) as client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        await monitor.reconcile(sid, notifications_complete=True)
        row = await get(sessions, sid)
        assert row.status == SubmissionStatus.submitted
        assert row.vk_monitor_evidence["wall_window_complete"] is False
        assert seen.count("wall.get") == 3
        async with sessions() as s, s.begin():
            await s.execute(
                update(Submission).where(Submission.id == sid).values(vk_suggested_post_id=None)
            )
        await monitor.reconcile(sid, notifications_complete=True)
        assert len(seen) == 4
        assert (await get(sessions, sid)).vk_monitor_evidence["result"] == "missing_receipt"


@pytest.mark.integration
async def test_worker_tick_polls_once_per_account_and_never_sends(sessions):
    sid, aid, *_ = await seed(sessions)
    client, seen = mock_client(old=SUGGESTION, notifications=[])
    async with client:
        monitor = PublicationMonitor(sessions, client, Tokens())
        await monitor.tick()
        await monitor.tick()
    assert [m for m, _ in seen] == ["notifications.get", "wall.getById"]
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted


@pytest.mark.integration
async def test_fallback_direct_same_object_and_confirmation_failure(sessions):
    sid, aid, *_ = await seed(sessions)
    async with mock_client(old={**PUBLISHED, "id": 4})[0] as client:
        await PublicationMonitor(sessions, client, Tokens()).reconcile(
            sid, notifications_complete=False
        )
    assert (await get(sessions, sid)).published_post_url == "https://vk.com/wall-242100737_4"


@pytest.mark.integration
async def test_fallback_candidate_must_confirm_again(sessions):
    sid, aid, *_ = await seed(sessions)
    async with mock_client(published={})[0] as client:
        await PublicationMonitor(sessions, client, Tokens()).reconcile(
            sid, notifications_complete=True
        )
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted


@pytest.mark.integration
async def test_concurrent_receipt_recording_counts_once(sessions):
    sid, aid, cid, mid = await seed(sessions)

    async def record_again():
        async with sessions() as s, s.begin():
            await record_suggested_submission(
                s,
                await s.get(Submission, sid),
                account_id=aid,
                receipt=WallPostReceipt(post_id=4),
                suggestion=WallPostDetails(**SUGGESTION),
            )

    await asyncio.gather(record_again(), record_again())
    async with sessions() as s:
        assert (await s.get(MediaAsset, mid)).usage_count == 1


@pytest.mark.integration
async def test_conflicting_receipt_rejected(sessions):
    sid, aid, *_ = await seed(sessions)
    async with sessions() as s, s.begin():
        with pytest.raises(ConflictError):
            await record_suggested_submission(
                s,
                await s.get(Submission, sid),
                account_id=aid,
                receipt=WallPostReceipt(post_id=6),
                suggestion=WallPostDetails(**{**SUGGESTION, "id": 6}),
            )


@pytest.mark.integration
async def test_pending_with_moderated_attachments_is_not_absence(sessions):
    sid, aid, *_ = await seed(sessions, expired=True)
    async with mock_client(old={**SUGGESTION, "attachments": []})[0] as client:
        await PublicationMonitor(sessions, client, Tokens()).reconcile(
            sid, notifications_complete=True
        )
    assert (await get(sessions, sid)).status == SubmissionStatus.submitted


def test_safe_evidence_omits_arbitrary_json():
    from dropgrid.services.publication import safe_evidence

    assert safe_evidence(
        {
            "result": "read_error",
            "error_message": "private-sentinel",
            "source": "private-sentinel",
            "raw_response": {"access_token": "private-sentinel"},
        }
    ) == {"result": "read_error"}
