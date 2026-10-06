import json
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
