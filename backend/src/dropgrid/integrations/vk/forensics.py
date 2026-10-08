"""Read-only receipt evidence. Does not change the production strict matcher."""

import re
from typing import Any

from dropgrid.integrations.vk.models import WallPostDetails

_MARKER = re.compile(r"\[DropGrid-test:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\]")


def summarize_post(
    post: WallPostDetails,
    *,
    owner_id: int,
    post_id: int,
    user_id: int,
    photo: tuple[int, int],
    audio: tuple[int, int],
) -> dict[str, Any]:
    photos: list[dict[str, int]] = []
    audios: list[dict[str, int]] = []
    kinds: list[str] = []
    for attachment in post.attachments:
        kind = attachment.get("type")
        kinds.append(
            kind
            if isinstance(kind, str)
            and kind
            in {
                "photo",
                "audio",
                "video",
                "doc",
                "link",
                "poll",
                "sticker",
                "market",
                "album",
                "wall",
                "wall_reply",
            }
            else "other"
        )
        if not isinstance(kind, str) or kind not in {"photo", "audio"}:
            continue
        value = attachment.get(kind)
        if not isinstance(value, dict):
            continue
        owner, identity = value.get("owner_id"), value.get("id")
        if type(owner) is int and type(identity) is int:
            (photos if kind == "photo" else audios).append({"owner_id": owner, "id": identity})
    evidence = {
        "receipt_id_match": post.id == post_id,
        "owner_match": post.owner_id == owner_id,
        "from_user_match": post.from_id == user_id,
        "marker_match": bool(_MARKER.search(post.text)),
        "photo_match": {"owner_id": photo[0], "id": photo[1]} in photos,
        "audio_match": {"owner_id": audio[0], "id": audio[1]} in audios,
    }
    return {
        "id": post.id,
        "owner_id": post.owner_id,
        "from_id": post.from_id,
        "date": post.date,
        "post_type": post.post_type
        if post.post_type in {"post", "copy", "reply", "postpone", "suggest"}
        else None,
        "is_pinned": post.is_pinned,
        "text_length": len(post.text),
        "attachment_types": kinds,
        "photos": photos,
        "audios": audios,
        **evidence,
    }


def classify_receipt(
    direct: list[dict[str, Any]],
    suggests: list[dict[str, Any]],
    normal: list[dict[str, Any]],
) -> str:
    def receipt(items: list[dict[str, Any]]) -> bool:
        return any(x["receipt_id_match"] and x["owner_match"] for x in items)

    def strong(items: list[dict[str, Any]]) -> bool:
        return any(
            all(x[k] for k in ("owner_match", "from_user_match", "marker_match", "photo_match"))
            for x in items
        )

    in_suggests, in_normal = receipt(suggests), receipt(normal)
    if in_suggests and in_normal:
        return "UNKNOWN"
    if in_suggests:
        return "SUGGESTED"
    if in_normal:
        return "PUBLISHED"
    if strong(suggests):
        return "SUGGESTED"
    return "EXISTS_UNKNOWN_PLACEMENT" if receipt(direct) else "UNKNOWN"
