import json
import logging
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from test_vk_client import NoWaitLimiter

from dropgrid.config import Settings
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import (
    VKInputError,
    VKProtocolError,
    VKTransportError,
    VKWriteDisabledError,
)
from dropgrid.integrations.vk.models import VKAttachment
from dropgrid.integrations.vk.photos import WallPhotoUploader, validate_upload_url

ACCOUNT = UUID(int=1)
TOKEN = "unit-photo-credential-sentinel"
CAPABILITY = "unit-upload-capability-sentinel"
PNG = b"\x89PNG\r\n\x1a\n" + b"test-photo-bytes"


async def test_complete_photo_pipeline(caplog):
    calls = []

    def api(request):
        calls.append(request.url.path)
        form = parse_qs(request.content.decode())
        assert form["access_token"] == [TOKEN] and form["group_id"] == ["123"]
        if request.url.path.endswith("getWallUploadServer"):
            return httpx.Response(
                200,
                json={
                    "response": {
                        "upload_url": f"https://pu.vk.com/upload?capability={CAPABILITY}",
                        "album_id": -14,
                        "user_id": 1,
                    }
                },
            )
        assert (
            form["server"] == ["9"]
            and form["photo"] == ['[{"photo":"uploaded"}]']
            and form["hash"] == ["hash-sentinel"]
        )
        return httpx.Response(
            200, json={"response": [{"id": 456, "owner_id": 1, "access_key": "saved_key"}]}
        )

    def upload(request):
        calls.append("multipart")
        assert request.url.host == "pu.vk.com" and request.method == "POST"
        assert b'name="photo"' in request.content and PNG in request.content
        assert b"image/png" in request.content and TOKEN.encode() not in request.content
        assert "authorization" not in request.headers
        return httpx.Response(
            200, json={"server": 9, "photo": '[{"photo":"uploaded"}]', "hash": "hash-sentinel"}
        )

    config = Settings(_env_file=None, vk_write_enabled=True)
    with caplog.at_level(logging.INFO):
        async with VKClient(
            config, transport=httpx.MockTransport(api), limiter=NoWaitLimiter()
        ) as client:
            async with WallPhotoUploader(client, transport=httpx.MockTransport(upload)) as uploader:
                result = await uploader.upload(
                    PNG,
                    mime_type="image/png",
                    community_id=123,
                    account_id=ACCOUNT,
                    access_token=TOKEN,
                )
    assert result == VKAttachment("photo", 1, 456, "saved_key")
    assert calls == [
        "/method/photos.getWallUploadServer",
        "multipart",
        "/method/photos.saveWallPhoto",
    ]
    assert TOKEN not in caplog.text and CAPABILITY not in caplog.text
    assert "hash-sentinel" not in caplog.text


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"", "image/png"),
        (b"text", "image/jpeg"),
        (PNG, "image/jpeg"),
        (PNG, "application/octet-stream"),
        (PNG * 10, "image/png"),
    ],
)
async def test_invalid_images_before_network(data, mime):
    def forbidden(request):
        pytest.fail("invalid image caused HTTP")

    config = Settings(_env_file=None, vk_write_enabled=True, vk_max_photo_bytes=100)
    async with VKClient(
        config, transport=httpx.MockTransport(forbidden), limiter=NoWaitLimiter()
    ) as client:
        async with WallPhotoUploader(client, transport=httpx.MockTransport(forbidden)) as uploader:
            with pytest.raises(VKInputError):
                await uploader.upload(
                    data, mime_type=mime, community_id=123, account_id=ACCOUNT, access_token=TOKEN
                )


async def test_disabled_upload_before_file_read(tmp_path):
    config = Settings(_env_file=None, vk_write_enabled=False)

    def forbidden(request):
        pytest.fail("disabled upload caused HTTP")

    async with VKClient(
        config, transport=httpx.MockTransport(forbidden), limiter=NoWaitLimiter()
    ) as client:
        async with WallPhotoUploader(client, transport=httpx.MockTransport(forbidden)) as uploader:
            with pytest.raises(VKWriteDisabledError):
                await uploader.upload(
                    tmp_path / "nonexistent.png",
                    community_id=123,
                    account_id=ACCOUNT,
                    access_token=TOKEN,
                )


@pytest.mark.parametrize(
    "url",
    [
        "http://pu.vk.com/upload",
        "https://vk.com.evil/upload",
        "https://127.0.0.1/upload",
        "https://u:p@pu.vk.com/upload",
        "https://pu.vk.com:444/upload",
        "https://pu.vk.com/upload#fragment",
    ],
)
def test_upload_origin_allowlist(url):
    with pytest.raises(VKProtocolError):
        validate_upload_url(url)


@pytest.mark.parametrize("failure", ["timeout", "status", "malformed", "empty_photo"])
async def test_upload_errors_sanitized_and_no_save(failure, caplog):
    calls = []

    def api(request):
        calls.append("server")
        return httpx.Response(
            200,
            json={
                "response": {
                    "upload_url": f"https://pu.vk.com/upload?secret={CAPABILITY}",
                    "album_id": -14,
                    "user_id": 1,
                }
            },
        )

    def upload(request):
        calls.append("upload")
        if failure == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        if failure == "status":
            return httpx.Response(500, text=TOKEN)
        if failure == "empty_photo":
            return httpx.Response(200, json={"server": 1, "photo": "[]", "hash": "hash"})
        return httpx.Response(200, json={"error": TOKEN})

    async with VKClient(
        Settings(_env_file=None, vk_write_enabled=True),
        transport=httpx.MockTransport(api),
        limiter=NoWaitLimiter(),
    ) as client:
        async with WallPhotoUploader(client, transport=httpx.MockTransport(upload)) as uploader:
            with pytest.raises((VKTransportError, VKProtocolError)) as error:
                await uploader.upload(PNG, community_id=123, account_id=ACCOUNT, access_token=TOKEN)
    assert calls == ["server", "upload"]
    assert (
        TOKEN not in str(error.value)
        and TOKEN not in json.dumps(error.value.as_dict())
        and TOKEN not in caplog.text
    )
    assert error.value.__context__ is None
