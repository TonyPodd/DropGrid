"""One explicit local MediaAsset experiment; never a campaign sender."""

import asyncio
import hashlib
from io import BytesIO
from uuid import UUID, uuid4

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.config import Settings
from dropgrid.db.models import Account, MediaAsset
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.errors import (
    VKAPIError,
    VKError,
    VKInputError,
    VKProtocolError,
    VKWriteDisabledError,
)
from dropgrid.integrations.vk.helpers import build_suggested_post_request, parse_vk_audio_reference
from dropgrid.integrations.vk.models import VKAttachment
from dropgrid.integrations.vk.photos import WallPhotoUploader, load_image
from dropgrid.photos.domain import PhotoError, PhotoPolicy
from dropgrid.photos.images import MediaStorage
from dropgrid.services.catalog import get_entity

TARGET = 242100737


def require_media_target(client: VKClient, community_id: int) -> None:
    client.require_diagnostic_target(community_id)
    if community_id != TARGET or client.settings.vk_test_allowed_community_ids != {TARGET}:
        raise VKWriteDisabledError(
            "wall.post", "Media diagnostic requires exactly target 242100737"
        )


def checked_media(asset: MediaAsset, storage: MediaStorage, max_bytes: int) -> bytes:
    try:
        data, mime = load_image(storage.path(asset.storage_key), asset.mime_type, max_bytes)
        if mime != "image/jpeg" or not asset.sha256:
            raise ValueError
        if hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ValueError
        with Image.open(BytesIO(data)) as image:
            if image.format != "JPEG" or image.width * image.height > PhotoPolicy().max_pixels:
                raise ValueError
            if (image.width, image.height) != (asset.width, asset.height):
                raise ValueError
            image.verify()
        return data
    except (OSError, ValueError, PhotoError, Image.DecompressionBombError):
        raise VKInputError(
            "media.asset", "MediaAsset file is missing, invalid or inconsistent"
        ) from None


def post_matches(
    item: dict[str, object], owner_id: int, photo: VKAttachment, audio: VKAttachment, marker: str
) -> bool:
    if item.get("owner_id") != owner_id or marker not in str(item.get("text", "")):
        return False
    attachments = item.get("attachments")
    if not isinstance(attachments, list):
        return False
    identities = set()
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        kind = attachment.get("type")
        value = attachment.get(kind) if isinstance(kind, str) else None
        if isinstance(value, dict):
            owner, media_id = value.get("owner_id"), value.get("id")
            if type(owner) is int and type(media_id) is int:
                identities.add((kind, owner, media_id))
    return all((a.type, a.owner_id, a.media_id) in identities for a in (photo, audio))


def classify(
    all_match: bool, suggests_match: bool, all_available: bool, suggests_available: bool
) -> str:
    if all_match and suggests_match:
        return "UNKNOWN"
    if all_match:
        return "PUBLISHED"
    if suggests_match:
        return "SUGGESTED"
    return "NOT_FOUND" if all_available and suggests_available else "UNKNOWN"


async def suggest_media(
    *,
    account_id: UUID,
    community_id: int,
    media_asset_id: UUID,
    track: str,
    caption: str,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    client: VKClient,
    tokens: TokenProvider,
    storage: MediaStorage,
    uploader: WallPhotoUploader,
) -> dict[str, object]:
    require_media_target(client, community_id)
    if settings.app_env != "development":
        raise VKWriteDisabledError("wall.post", "Media diagnostic is local development only")
    if len(caption) > 3500:
        raise VKInputError("wall.post", "Diagnostic caption is too long")
    audio = parse_vk_audio_reference(track)
    async with sessions() as session:
        account = await get_entity(session, Account, account_id)
        asset = await get_entity(session, MediaAsset, media_asset_id)
        if not asset.enabled:
            raise VKInputError("media.asset", "MediaAsset is disabled")
        expected_user = account.vk_user_id
    data = await asyncio.to_thread(checked_media, asset, storage, settings.vk_max_photo_bytes)
    token = await tokens.get_token(account_id)
    user = await client.get_current_user(access_token=token, account_id=account_id)
    if expected_user is None or user.id != expected_user:
        raise VKProtocolError("users.get", "Account identity mismatch")
    group = await client.resolve_community(
        f"club{TARGET}", access_token=token, account_id=account_id
    )
    if group.id != TARGET:
        raise VKProtocolError("groups.getById", "Resolved community does not match target")
    if group.is_admin != 0:
        raise VKWriteDisabledError("wall.post", "Suggestion experiment requires a non-admin user")
    await client.get_wall_posts(
        f"club{TARGET}", access_token=token, account_id=account_id, count=20
    )
    marker = f"[DropGrid-test:{uuid4()}]"
    result: dict[str, object] = {
        "target": TARGET,
        "user_id": user.id,
        "media_asset_id": str(media_asset_id),
        "classification": "UNKNOWN",
        "all_wall_match": False,
        "suggests_wall_match": None,
        "wall_post": "NOT_RUN",
        "error": None,
        "write_attempts": 0,
        "wall_post_attempts": 0,
        "photo_upload_attempts": 0,
    }
    result["photo_upload_attempts"] = 1
    photo = await uploader.upload(
        data, community_id=TARGET, account_id=account_id, access_token=token, mime_type="image/jpeg"
    )
    result["photo_attachment"] = {"owner_id": photo.owner_id, "id": photo.media_id}
    result["write_attempts"] = 3
    payload = build_suggested_post_request(
        TARGET, caption=f"{caption}\n{marker}", photo=photo, audio=audio
    )
    result["wall_post_attempts"], result["write_attempts"] = 1, 4
    try:
        receipt = await client.create_wall_post(payload, access_token=token, account_id=account_id)
    except VKError as error:
        result.update(
            wall_post="ERROR",
            error=error.as_dict(),
            classification="REJECTED" if isinstance(error, VKAPIError) else "UNKNOWN",
        )
        return result
    result.update(wall_post="SUCCESS", post_id=receipt.post_id)
    evidence: dict[str, object] = {}
    found, available = {}, {}
    for name, suggests in (("all", False), ("suggests", True)):
        try:
            posts = await client.get_wall_posts(
                f"club{TARGET}",
                access_token=token,
                account_id=account_id,
                count=100,
                suggests=suggests,
            )
            matches = [p for p in posts.items if post_matches(p, -TARGET, photo, audio, marker)]
            found[name], available[name] = bool(matches), True
            evidence[name] = {
                "returned": len(posts.items),
                "matched": bool(matches),
                "receipt_id_match": any(p.get("id") == receipt.post_id for p in matches),
            }
        except VKError as error:
            found[name], available[name] = False, False
            evidence[name] = {"error": error.as_dict()}
    result.update(
        classification=classify(
            found["all"], found["suggests"], available["all"], available["suggests"]
        ),
        all_wall_match=found["all"] if available["all"] else None,
        suggests_wall_match=found["suggests"] if available["suggests"] else None,
        evidence=evidence,
    )
    return result
