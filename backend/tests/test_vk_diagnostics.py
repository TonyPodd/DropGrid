import json
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from test_vk_client import NoWaitLimiter

from dropgrid.config import Settings
from dropgrid.integrations.vk import diagnostics
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import VKWriteDisabledError
from dropgrid.integrations.vk.photos import WallPhotoUploader


@pytest.mark.parametrize(
    ("enabled", "allowed", "target"), [(False, {123}, 123), (True, set(), 123), (True, {123}, 456)]
)
async def test_diagnostic_guards_before_any_network(enabled, allowed, target):
    args = diagnostics.parser().parse_args(
        [
            "suggest",
            "--community-id",
            str(target),
            "--track",
            "audio1_2",
            "--image",
            "nonexistent.png",
        ]
    )
    config = Settings(
        _env_file=None, vk_write_enabled=enabled, vk_test_allowed_community_ids=allowed
    )

    def forbidden(request):
        pytest.fail("Diagnostic guard reached network")

    async with VKClient(
        config, transport=httpx.MockTransport(forbidden), limiter=NoWaitLimiter()
    ) as client:
        with pytest.raises(VKWriteDisabledError):
            await diagnostics.execute(args, config, client)


def test_cli_rejects_token_without_echo(capsys):
    with pytest.raises(SystemExit) as error:
        diagnostics.parser().parse_args(["account", "--token", "argument-credential-sentinel"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "argument-credential-sentinel" not in output.out + output.err


async def test_mocked_single_write_preflight_and_exactly_one_post(tmp_path, monkeypatch):
    image = tmp_path / "test.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nmock-image")
    args = diagnostics.parser().parse_args(
        [
            "suggest",
            "--community-id",
            "123",
            "--track",
            "https://vk.ru/audio1_2",
            "--image",
            str(image),
        ]
    )
    config = Settings(
        _env_file=None,
        app_env="development",
        vk_write_enabled=True,
        vk_test_allowed_community_ids={123},
        vk_test_account_id=UUID(int=1),
        vk_test_access_token="diagnostic-test-sentinel",
    )
    calls = []

    def api(request):
        method = request.url.path.rsplit("/", 1)[1]
        calls.append(method)
        responses = {
            "users.get": [{"id": 1}],
            "groups.getById": {"groups": [{"id": 123}]},
            "wall.get": {"count": 0, "items": []},
            "photos.getWallUploadServer": {
                "upload_url": "https://pu.vk.com/upload",
                "album_id": -14,
                "user_id": 1,
            },
            "photos.saveWallPhoto": [{"owner_id": 1, "id": 7}],
            "wall.post": {"post_id": 8},
        }
        return httpx.Response(200, json={"response": responses[method]})

    upload = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"server": 1, "photo": "uploaded", "hash": "hash"})
    )
    monkeypatch.setattr(
        diagnostics, "WallPhotoUploader", lambda client: WallPhotoUploader(client, transport=upload)
    )
    async with VKClient(
        config, transport=httpx.MockTransport(api), limiter=NoWaitLimiter()
    ) as client:
        result = await diagnostics.execute(args, config, client)
    assert result["post_id"] == 8 and "unverified" in result["placement"]
    assert calls == [
        "users.get",
        "groups.getById",
        "wall.get",
        "photos.getWallUploadServer",
        "photos.saveWallPhoto",
        "wall.post",
    ]
    assert "diagnostic-test-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("enabled,allowed", [(False, {123}), (True, set()), (True, {123, 456})])
async def test_text_diagnostic_single_target_guard_before_network(enabled, allowed):
    args = diagnostics.parser().parse_args(["suggest-text", "--community-id", "123"])
    config = Settings(
        _env_file=None, vk_write_enabled=enabled, vk_test_allowed_community_ids=allowed
    )

    def forbidden(request):
        pytest.fail("Text guard reached network")

    async with VKClient(config, transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(VKWriteDisabledError):
            await diagnostics.execute(args, config, client)


@pytest.mark.parametrize("outcome", ["success", "permission", "timeout", "admin", "wrong-target"])
async def test_text_diagnostic_exact_payload_single_attempt_no_media(outcome):
    args = diagnostics.parser().parse_args(["suggest-text", "--community-id", "123"])
    config = Settings(
        _env_file=None,
        app_env="development",
        vk_write_enabled=True,
        vk_test_allowed_community_ids={123},
        vk_test_account_id=UUID(int=1),
        vk_test_access_token="text-diagnostic-sentinel",
        vk_max_attempts=3,
    )
    calls = []

    def api(request):
        method = request.url.path.rsplit("/", 1)[1]
        calls.append(method)
        if method == "wall.post":
            form = parse_qs(request.content.decode())
            assert form == {
                "owner_id": ["-123"],
                "from_group": ["0"],
                "message": ["DropGrid integration test"],
                "access_token": ["text-diagnostic-sentinel"],
                "v": [config.vk_api_version],
            }
            if outcome == "timeout":
                raise httpx.ReadTimeout("synthetic", request=request)
            if outcome == "permission":
                return httpx.Response(200, json={"error": {"error_code": 15}})
            return httpx.Response(200, json={"response": {"post_id": 8}})
        responses = {
            "users.get": [{"id": 1}],
            "groups.getById": {
                "groups": [
                    {
                        "id": 456 if outcome == "wrong-target" else 123,
                        "is_admin": 1 if outcome == "admin" else 0,
                        "is_member": 0,
                    }
                ]
            },
            "wall.get": {"count": 0, "items": []},
        }
        return httpx.Response(200, json={"response": responses[method]})

    from dropgrid.integrations.vk.errors import (
        VKCredentialUnavailableError,
        VKPermissionError,
        VKTransportError,
    )

    errors = {
        "permission": VKPermissionError,
        "timeout": VKTransportError,
        "admin": VKWriteDisabledError,
        "wrong-target": VKCredentialUnavailableError,
    }
    async with VKClient(
        config, transport=httpx.MockTransport(api), limiter=NoWaitLimiter()
    ) as client:
        if outcome == "success":
            result = await diagnostics.execute(args, config, client)
            assert result["post_id"] == 8
            assert "text-diagnostic-sentinel" not in json.dumps(result)
        else:
            with pytest.raises(errors[outcome]):
                await diagnostics.execute(args, config, client)
    expected = ["users.get", "groups.getById"]
    if outcome not in {"admin", "wrong-target"}:
        expected += ["wall.get", "wall.post"]
    assert calls == expected
