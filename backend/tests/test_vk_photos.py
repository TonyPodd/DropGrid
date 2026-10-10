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
    stages = [r for r in caplog.records if r.message.startswith("vk_photo_stage")]
    assert len(stages) == 3
    assert all("success=True" in r.message for r in stages)
    assert "stage=multipart success=True http_status=200" in stages[1].message
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
    caplog.set_level(logging.INFO)
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
    stages = [r.message for r in caplog.records if r.message.startswith("vk_photo_stage")]
    assert len(stages) == 2 and "stage=multipart success=False" in stages[1]
    assert "stage=save_wall_photo" not in caplog.text
    if failure in {"malformed", "empty_photo"}:
        assert "http_status=200" in stages[1] and "error_class=VKProtocolError" in stages[1]
    if failure == "status":
        assert "http_status=500" in stages[1]
    if failure == "timeout":
        assert "transport_class=ReadTimeout" in stages[1]
    assert (
        TOKEN not in str(error.value)
        and TOKEN not in json.dumps(error.value.as_dict())
        and TOKEN not in caplog.text
    )
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "kind,category",
    [
        (httpx.ConnectError, "connect_error"),
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.WriteError, "write_error"),
        (httpx.WriteTimeout, "write_timeout"),
        (httpx.ReadError, "read_error"),
        (httpx.PoolTimeout, "pool_timeout"),
        (httpx.RemoteProtocolError, "protocol_error"),
    ],
)
async def test_upload_transport_categories_never_leak_or_retry(kind, category, caplog):
    calls = []
    url = f"https://pu.vk.com/private-path?secret={CAPABILITY}"

    def api(request):
        calls.append("server")
        return httpx.Response(
            200, json={"response": {"upload_url": url, "album_id": 1, "user_id": 1}}
        )

    def upload(request):
        calls.append("multipart")
        assert request.method == "POST"
        assert b'name="photo"; filename="upload.jpg"' in request.content
        assert b"Content-Type: image/jpeg" in request.content
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert TOKEN.encode() not in request.content
        raise kind(f"{TOKEN} {url}", request=request)

    async with (
        VKClient(
            Settings(_env_file=None, vk_write_enabled=True, vk_max_attempts=3),
            transport=httpx.MockTransport(api),
            limiter=NoWaitLimiter(),
        ) as client,
        WallPhotoUploader(client, transport=httpx.MockTransport(upload)) as uploader,
    ):
        with pytest.raises(VKTransportError) as error:
            await uploader.upload(
                b"\xff\xd8\xffjpeg", community_id=123, account_id=ACCOUNT, access_token=TOKEN
            )
    detail = error.value.as_dict()
    assert detail["transport_category"] == category
    assert detail["exception_type"] == kind.__name__
    assert detail["stage"] == "multipart" and detail["upload_host"] == "pu.vk.com"
    assert error.value.__context__ is None and error.value.__cause__ is None
    for private in (TOKEN, CAPABILITY, url, "private-path", "secret="):
        assert (
            private not in json.dumps(detail)
            and private not in str(error.value)
            and private not in caplog.text
        )
    assert calls == ["server", "multipart"]  # No save and no automatic retry.


@pytest.mark.parametrize(
    "cause_name,expected",
    [("dns", "dns"), ("certificate", "tls_certificate"), ("route", "network_unreachable")],
)
def test_upload_transport_cause_metadata_is_fixed(cause_name, expected):
    import errno
    import socket
    import ssl

    from dropgrid.integrations.vk.photos import upload_transport_error

    causes = {
        "dns": socket.gaierror(-2, CAPABILITY),
        "certificate": ssl.SSLCertVerificationError(1, CAPABILITY),
        "route": OSError(errno.ENETUNREACH, CAPABILITY),
    }
    original = httpx.ConnectError(TOKEN)
    original.__cause__ = causes[cause_name]
    safe = upload_transport_error(original, f"https://pu.vk.com/upload?secret={CAPABILITY}")
    assert safe.as_dict()["cause_category"] == expected
    assert TOKEN not in str(safe.as_dict()) and CAPABILITY not in str(safe.as_dict())


async def test_upload_read_timeout_is_separate_from_api_and_other_phases():
    config = Settings(
        _env_file=None, vk_write_enabled=True, vk_timeout_seconds=10, vk_upload_timeout_seconds=37
    )
    calls = []

    def api(request):
        calls.append(request.url.path)
        assert request.extensions["timeout"]["read"] == 10
        if request.url.path.endswith("getWallUploadServer"):
            data = {"upload_url": "https://pu.vk.com/upload", "album_id": 1, "user_id": 1}
        else:
            data = [{"id": 456, "owner_id": 1}]
        return httpx.Response(200, json={"response": data})

    def upload(request):
        assert request.extensions["timeout"] == {"connect": 10, "read": 37, "write": 10, "pool": 10}
        return httpx.Response(200, json={"server": 1, "photo": "uploaded", "hash": "hash"})

    async with (
        VKClient(config, transport=httpx.MockTransport(api), limiter=NoWaitLimiter()) as client,
        WallPhotoUploader(client, transport=httpx.MockTransport(upload)) as uploader,
    ):
        await uploader.upload(PNG, community_id=123, account_id=ACCOUNT, access_token=TOKEN)
    assert len(calls) == 2


@pytest.mark.parametrize("kind", ["read", "write"])
def test_nonblocking_ssl_wait_is_not_tls_failure(kind):
    import ssl

    from dropgrid.integrations.vk.photos import upload_transport_error

    original = httpx.ReadTimeout(TOKEN)
    original.__context__ = (ssl.SSLWantReadError if kind == "read" else ssl.SSLWantWriteError)(
        2, CAPABILITY
    )
    detail = upload_transport_error(original, "https://pu.vk.com/upload").as_dict()
    assert (
        detail["transport_category"] == "read_timeout" and detail["cause_category"] == "unavailable"
    )
