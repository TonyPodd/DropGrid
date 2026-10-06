import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dropgrid.integrations.vk.errors import VKInputError
from dropgrid.integrations.vk.models import VKAttachment

_AUDIO = re.compile(r"audio(-?\d+)_(\d+)(?:_([A-Za-z0-9_-]+))?")


def parse_vk_audio_reference(value: str) -> VKAttachment:
    reference = value.strip()
    if "://" in reference or "/" in reference or reference.startswith(("vk.com", "vk.ru")):
        parsed = urlsplit(reference if "://" in reference else "https://" + reference)
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.netloc.lower()
            not in {"vk.com", "vk.ru", "www.vk.com", "www.vk.ru", "m.vk.com", "m.vk.ru"}
            or parsed.fragment
        ):
            raise VKInputError("audio.parse", "Expected a VK audio reference")
        path = parsed.path.strip("/")
        query = parse_qs(parsed.query)
        if path == "audio" and set(query) == {"z"} and len(query["z"]) == 1:
            reference = query["z"][0].split("/", 1)[0]
        elif not parsed.query:
            reference = path
        else:
            raise VKInputError("audio.parse", "Unsupported VK audio URL parameters")
    match = _AUDIO.fullmatch(reference)
    if match is None:
        raise VKInputError("audio.parse", "Malformed VK audio reference")
    try:
        return VKAttachment("audio", int(match[1]), int(match[2]), match[3])
    except ValueError:
        raise VKInputError("audio.parse", "Invalid VK audio identity") from None


def build_suggested_post_request(
    community_id: int, *, caption: str | None, photo: VKAttachment, audio: VKAttachment
) -> dict[str, Any]:
    if type(community_id) is not int or not 0 < community_id <= 2**63 - 1:
        raise VKInputError("wall.post", "Expected a positive community ID")
    if photo.type != "photo" or audio.type != "audio":
        raise VKInputError("wall.post", "Expected photo then audio")
    params: dict[str, Any] = {
        "owner_id": -community_id,
        "from_group": 0,
        "attachments": f"{photo.serialize()},{audio.serialize()}",
    }
    if caption:
        params["message"] = caption
    return params


def post_contains_audio(post: object, target_audio: VKAttachment) -> bool:
    """Search the post and repost copy_history, safely handling malformed/cyclic input."""
    if target_audio.type != "audio":
        return False
    pending = [post]
    visited: set[int] = set()
    while pending:
        item = pending.pop()
        if not isinstance(item, Mapping) or id(item) in visited:
            continue
        visited.add(id(item))
        attachments = item.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, Mapping) or attachment.get("type") != "audio":
                    continue
                audio = attachment.get("audio")
                if (
                    isinstance(audio, Mapping)
                    and type(audio.get("owner_id")) is int
                    and type(audio.get("id")) is int
                    and audio["owner_id"] == target_audio.owner_id
                    and audio["id"] == target_audio.media_id
                ):
                    return True
        response = item.get("response")
        if isinstance(response, Mapping):
            pending.append(response)
        wall_items = item.get("items")
        if isinstance(wall_items, list):
            pending.extend(wall_items)
        history = item.get("copy_history")
        if isinstance(history, list):
            pending.extend(history)
    return False
