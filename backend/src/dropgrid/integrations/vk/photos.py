import asyncio
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr, TypeAdapter

from dropgrid.integrations.vk.client import VKClient, parse_response
from dropgrid.integrations.vk.errors import VKInputError, VKProtocolError, VKTransportError
from dropgrid.integrations.vk.models import (
    SavedPhoto,
    VKAttachment,
    WallUploadResult,
    WallUploadServer,
)

_UPLOAD_SUFFIXES = ("vk.com", "vk.ru", "vkuserphoto.ru", "userapi.com", "vk-cdn.net")


def validate_upload_url(value: str) -> str:
    valid = False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        valid = (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in {None, 443}
            and not parsed.fragment
            and any(host == suffix or host.endswith("." + suffix) for suffix in _UPLOAD_SUFFIXES)
        )
    except ValueError:
        pass
    if not valid:
        raise VKProtocolError("photo.upload", "Untrusted VK upload server URL")
    return value


def load_image(image: bytes | Path, mime_type: str | None, max_bytes: int) -> tuple[bytes, str]:
    data: bytes
    error: VKInputError | None = None
    if isinstance(image, Path):
        if mime_type is None:
            mime_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(
                image.suffix.lower()
            )
        try:
            with image.open("rb") as file:
                data = file.read(max_bytes + 1)
        except OSError:
            error = VKInputError("photo.upload", "Cannot read local image")
            data = b""
    else:
        data = image
    if error is not None:
        raise error
    if not data or len(data) > max_bytes:
        raise VKInputError("photo.upload", "Image is empty or exceeds configured size limit")
    detected = (
        "image/png"
        if data.startswith(b"\x89PNG\r\n\x1a\n")
        else "image/jpeg"
        if data.startswith(b"\xff\xd8\xff")
        else None
    )
    if detected is None or mime_type not in {None, detected}:
        raise VKInputError(
            "photo.upload", "Only matching JPEG/PNG image signatures and MIME are accepted"
        )
    return data, detected


class WallPhotoUploader:
    """Isolated upload session: no API token, cookies or auth sent to upload servers."""

    def __init__(
        self,
        client: VKClient,
        *,
        upload_http: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if upload_http is not None and transport is not None:
            raise ValueError("Provide upload client or transport, not both")
        self.client = client
        self._owns_http = upload_http is None
        self.http = (
            upload_http
            if upload_http is not None
            else httpx.AsyncClient(
                transport=transport,
                timeout=client.settings.vk_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            )
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def upload(
        self,
        image: bytes | Path,
        *,
        community_id: int,
        account_id: UUID,
        access_token: str | SecretStr,
        mime_type: str | None = None,
    ) -> VKAttachment:
        self.client.require_write("photo.upload")
        if type(community_id) is not int or not 0 < community_id <= 2**63 - 1:
            raise VKInputError("photo.upload", "Expected a positive community ID")
        if self.http.params or self.http.auth is not None or "authorization" in self.http.headers:
            raise VKInputError(
                "photo.upload", "Upload client must not define authorization or query parameters"
            )
        data, mime = await asyncio.to_thread(
            load_image, image, mime_type, self.client.settings.vk_max_photo_bytes
        )
        response = await self.client.call(
            "photos.getWallUploadServer",
            access_token=access_token,
            account_id=account_id,
            params={"group_id": community_id},
        )
        server = parse_response(
            TypeAdapter(WallUploadServer), response["response"], "photos.getWallUploadServer"
        )
        url = validate_upload_url(server.upload_url)
        await self.client.limiter.acquire(account_id)
        failure: VKTransportError | None = None
        uploaded: object = None
        try:
            result = await self.http.post(
                url,
                files={
                    "photo": ("upload.png" if mime == "image/png" else "upload.jpg", data, mime)
                },
                timeout=self.client.settings.vk_timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            failure = VKTransportError(
                "photo.upload", "Photo upload transport failed; outcome uncertain"
            )
        else:
            if result.status_code != 200:
                failure = VKTransportError("photo.upload", "Photo upload HTTP request rejected")
            else:
                try:
                    uploaded = result.json()
                except ValueError:
                    pass
        if failure is not None:
            raise failure
        upload = parse_response(TypeAdapter(WallUploadResult), uploaded, "photo.upload")
        if upload.photo.strip() in {"[]", "null"}:
            raise VKProtocolError("photo.upload", "Upload server did not accept the image")
        saved = await self.client.call(
            "photos.saveWallPhoto",
            access_token=access_token,
            account_id=account_id,
            params={
                "group_id": community_id,
                "server": upload.server,
                "photo": upload.photo,
                "hash": upload.hash,
            },
        )
        photos = parse_response(
            TypeAdapter(list[SavedPhoto]), saved["response"], "photos.saveWallPhoto"
        )
        if len(photos) != 1:
            raise VKProtocolError("photos.saveWallPhoto", "Expected exactly one saved photo")
        return photos[0].attachment()
