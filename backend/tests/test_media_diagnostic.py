from collections import Counter
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from photo_fixtures import image_bytes
from pydantic import SecretStr
from test_vk_client import NoWaitLimiter

from dropgrid.config import Settings
from dropgrid.db.models import Account, MediaAsset
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.diagnostics import execute, parser
from dropgrid.integrations.vk.errors import VKError, VKWriteDisabledError
from dropgrid.integrations.vk.media_diagnostic import (
    TARGET,
    classify,
    post_matches,
    require_media_target,
    suggest_media,
)
from dropgrid.integrations.vk.models import VKAttachment
from dropgrid.integrations.vk.photos import WallPhotoUploader
from dropgrid.photos.domain import PhotoPolicy
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.services.catalog import NotFoundError


class Tokens:
    async def get_token(self, account_id):
        return SecretStr("mock-media-token")


@pytest.mark.parametrize(
    "enabled,allowed,target",
    [
        (False, {TARGET}, TARGET),
        (True, set(), TARGET),
        (True, {TARGET, 123}, TARGET),
        (True, {123}, 123),
        (True, {TARGET}, 123),
    ],
)
def test_exact_target_guard(enabled, allowed, target):
    # Guard is synchronous: no database, token, file or HTTP work can run first.
    client = VKClient(
        Settings(_env_file=None, vk_write_enabled=enabled, vk_test_allowed_community_ids=allowed)
    )
    with pytest.raises(VKWriteDisabledError):
        require_media_target(client, target)


@pytest.mark.parametrize("enabled,allowed", [(False, {TARGET}), (True, {TARGET, 123})])
async def test_cli_guard_runs_before_database(monkeypatch, enabled, allowed):
    def forbidden_database(*args):
        pytest.fail("Database must not be created before guard passes")

    monkeypatch.setattr("dropgrid.db.session.Database", forbidden_database)
    args = parser().parse_args(
        [
            "suggest-media",
            "--account-id",
            str(uuid4()),
            "--community-id",
            str(TARGET),
            "--media-asset-id",
            str(uuid4()),
            "--track",
            "audio123_987",
        ]
    )
    config = Settings(
        _env_file=None, vk_write_enabled=enabled, vk_test_allowed_community_ids=allowed
    )
    async with VKClient(config) as client:
        with pytest.raises(VKWriteDisabledError):
            await execute(args, config, client)


@pytest.mark.parametrize(
    "all_match,suggests_match,all_ok,suggests_ok,expected",
    [
        (True, False, True, False, "PUBLISHED"),
        (False, True, True, True, "SUGGESTED"),
        (False, False, True, False, "UNKNOWN"),
        (False, False, False, True, "UNKNOWN"),
        (False, False, True, True, "NOT_FOUND"),
        (True, True, True, True, "UNKNOWN"),
    ],
)
def test_classification(all_match, suggests_match, all_ok, suggests_ok, expected):
    assert classify(all_match, suggests_match, all_ok, suggests_ok) == expected


def test_match_requires_owner_marker_and_both_attachments():
    photo = VKAttachment("photo", 123, 456)
    audio = VKAttachment("audio", 123, 789)
    item = {
        "id": 1,
        "owner_id": -TARGET,
        "text": "unique-marker",
        "attachments": [
            {"type": "photo", "photo": {"owner_id": 123, "id": 456}},
            {"type": "audio", "audio": {"owner_id": 123, "id": 789}},
        ],
    }
    assert post_matches(item, -TARGET, photo, audio, "unique-marker")
    for change in (
        {"owner_id": -123},
        {"text": "other"},
        {"attachments": []},
        {"attachments": [None]},
    ):
        assert not post_matches({**item, **change}, -TARGET, photo, audio, "unique-marker")


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("published", "PUBLISHED"),
        ("suggested", "SUGGESTED"),
        ("empty", "NOT_FOUND"),
        ("denied", "UNKNOWN"),
        ("rejected", "REJECTED"),
        ("timeout", "UNKNOWN"),
    ],
)
async def test_media_pipeline_single_write_and_readback(sessions, tmp_path, scenario, expected):
    storage = LocalMediaStorage(tmp_path)
    image = normalize_image(image_bytes(), PhotoPolicy())
    key = storage.write(image)
    async with sessions() as db, db.begin():
        a = Account(name="User", vk_user_id=123)
        db.add(a)
        m = MediaAsset(
            storage_key=key,
            category="собаки",
            mime_type="image/jpeg",
            sha256=image.sha256,
            width=image.width,
            height=image.height,
        )
        db.add(m)
        await db.flush()
        aid, mid = a.id, m.id
    calls = []
    wall = []
    posted = {}

    def handle(r):
        method = r.url.path.split("/")[-1]
        calls.append(method)
        if r.url.host == "upload.vk.com":
            assert "access_token" not in str(r.url) and b"mock-media-token" not in r.content
            return httpx.Response(200, json={"server": 1, "photo": "uploaded", "hash": "hash"})
        form = parse_qs(r.content.decode())
        if method == "users.get":
            data = [{"id": 123}]
        elif method == "groups.getById":
            data = {"groups": [{"id": TARGET, "is_admin": 0, "is_member": 1}]}
        elif method == "photos.getWallUploadServer":
            data = {"upload_url": "https://upload.vk.com/upload", "album_id": 1, "user_id": 123}
        elif method == "photos.saveWallPhoto":
            data = [{"id": 456, "owner_id": 123, "access_key": "photo_key"}]
        elif method == "wall.post":
            posted.update(form)
            if scenario == "rejected":
                return httpx.Response(
                    200,
                    json={
                        "error": {
                            "error_code": 15,
                            "error_subcode": 1134,
                            "error_msg": "mock-media-token",
                        }
                    },
                )
            if scenario == "timeout":
                raise httpx.ReadTimeout("upstream token", request=r)
            data = {"post_id": 0}  # Zero receipt is not proof of placement.
        elif method == "wall.get":
            wall.append(form["filter"][0])
            is_after = bool(posted)
            if is_after and scenario == "denied" and form["filter"] == ["suggests"]:
                return httpx.Response(200, json={"error": {"error_code": 15}})
            item = {
                "id": 789,
                "owner_id": -TARGET,
                "text": posted.get("message", [""])[0],
                "attachments": [
                    {"type": "photo", "photo": {"owner_id": 123, "id": 456}},
                    {"type": "audio", "audio": {"owner_id": 123, "id": 987}},
                ],
            }
            match = is_after and (
                (scenario == "published" and form["filter"] == ["all"])
                or (scenario == "suggested" and form["filter"] == ["suggests"])
            )
            data = {"count": int(match), "items": [item] if match else []}
        else:
            pytest.fail("Unexpected method")
        return httpx.Response(200, json={"response": data})

    config = Settings(
        _env_file=None,
        vk_write_enabled=True,
        vk_test_allowed_community_ids={TARGET},
        vk_max_attempts=3,
    )
    transport = httpx.MockTransport(handle)
    async with (
        VKClient(config, transport=transport, limiter=NoWaitLimiter()) as vk,
        WallPhotoUploader(vk, transport=transport) as uploader,
    ):
        result = await suggest_media(
            account_id=aid,
            community_id=TARGET,
            media_asset_id=mid,
            track="audio123_987_audio_key",
            caption="Test",
            settings=config,
            sessions=sessions,
            client=vk,
            tokens=Tokens(),
            storage=storage,
            uploader=uploader,
        )
    assert result["classification"] == expected and "mock-media-token" not in str(result)
    counts = Counter(calls)
    assert (
        counts["wall.post"]
        == counts["photos.getWallUploadServer"]
        == counts["photos.saveWallPhoto"]
        == counts["upload"]
        == 1
    )
    assert (
        calls[:3] == ["users.get", "groups.getById", "wall.get"]
        and result["wall_post_attempts"] == 1
    )
    assert posted["owner_id"] == [str(-TARGET)] and posted["from_group"] == ["0"]
    assert posted["attachments"] == ["photo123_456_photo_key,audio123_987_audio_key"]
    assert "publish_date" not in posted and "post_id" not in posted
    if scenario == "rejected":
        assert result["error"]["error_subcode"] == 1134
    if scenario in ("rejected", "timeout"):
        assert wall == ["all"]
    else:
        assert wall == ["all", "all", "suggests"]
    async with sessions() as db:
        m = await db.get(MediaAsset, mid)
        assert m.usage_count == 0 and m.last_used_at is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "disabled",
        "traversal",
        "corrupt",
        "absent_file",
        "identity",
        "target",
        "admin",
        "unknown_role",
    ],
)
async def test_preflight_failures_never_write(sessions, tmp_path, problem):
    storage = LocalMediaStorage(tmp_path)
    image = normalize_image(image_bytes(), PhotoPolicy())
    key = storage.write(image)
    async with sessions() as db, db.begin():
        a = Account(name="User", vk_user_id=123)
        db.add(a)
        m = MediaAsset(
            storage_key="../escape" if problem == "traversal" else key,
            enabled=problem != "disabled",
            mime_type="image/jpeg",
            sha256="0" * 64 if problem == "corrupt" else image.sha256,
            width=image.width,
            height=image.height,
        )
        db.add(m)
        await db.flush()
        aid, mid = a.id, m.id
    if problem == "missing":
        mid = uuid4()
    if problem == "absent_file":
        storage.path(key).unlink()
    calls = []

    def handle(r):
        method = r.url.path.split("/")[-1]
        calls.append(method)
        assert method in ("users.get", "groups.getById")
        return httpx.Response(
            200,
            json={"response": [{"id": 456 if problem == "identity" else 123}]}
            if method == "users.get"
            else {
                "response": {
                    "groups": [
                        {
                            "id": 123 if problem == "target" else TARGET,
                            "is_admin": None
                            if problem == "unknown_role"
                            else 1
                            if problem == "admin"
                            else 0,
                        }
                    ]
                }
            },
        )

    config = Settings(_env_file=None, vk_write_enabled=True, vk_test_allowed_community_ids={TARGET})
    async with (
        VKClient(config, transport=httpx.MockTransport(handle), limiter=NoWaitLimiter()) as vk,
        WallPhotoUploader(vk) as uploader,
    ):
        with pytest.raises((VKError, NotFoundError)):
            await suggest_media(
                account_id=aid,
                community_id=TARGET,
                media_asset_id=mid,
                track="audio123_987",
                caption="Test",
                settings=config,
                sessions=sessions,
                client=vk,
                tokens=Tokens(),
                storage=storage,
                uploader=uploader,
            )
    if problem not in ("identity", "target", "admin", "unknown_role"):
        assert not calls
