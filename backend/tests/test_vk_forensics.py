from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from test_vk_client import NoWaitLimiter

from dropgrid.config import Settings
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import VKProtocolError
from dropgrid.integrations.vk.forensics import classify_receipt, summarize_post
from dropgrid.integrations.vk.models import WallPostDetails

MARKER = "[DropGrid-test:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee]"
TOKEN = "fake-forensic-token"
KEY = "private-attachment-key"


def item(**changes):
    return WallPostDetails.model_validate(
        {
            "id": 4,
            "owner_id": -242100737,
            "from_id": 615459987,
            "date": 123,
            "post_type": "suggest",
            "text": MARKER,
            "attachments": [
                {
                    "type": "photo",
                    "photo": {"owner_id": 615459987, "id": 457239318, "access_key": KEY},
                },
                {
                    "type": "audio",
                    "audio": {"owner_id": 2000410139, "id": 456245636, "access_key": KEY},
                },
            ],
            **changes,
        }
    )


def summary(post):
    return summarize_post(
        post,
        owner_id=-242100737,
        post_id=4,
        user_id=615459987,
        photo=(615459987, 457239318),
        audio=(2000410139, 456245636),
    )


@pytest.mark.parametrize("response", [{"items": [item().model_dump()]}, {"items": []}])
async def test_get_by_id_is_typed_read_without_write_guards(response):
    def handle(request):
        assert request.url.path.endswith("wall.getById")
        form = parse_qs(request.content.decode())
        assert form["posts"] == ["-242100737_4"] and form["extended"] == ["0"]
        return httpx.Response(200, json={"response": response})

    async with VKClient(
        Settings(_env_file=None, vk_write_enabled=False),
        transport=httpx.MockTransport(handle),
        limiter=NoWaitLimiter(),
    ) as client:
        posts = await client.get_wall_post_by_id(
            -242100737, 4, access_token=TOKEN, account_id=UUID(int=1)
        )
    assert len(posts.items) == len(response["items"])
    if posts.items:
        assert posts.items[0].id == 4 and posts.items[0].owner_id == -242100737
        assert KEY not in str(summary(posts.items[0]))


@pytest.mark.parametrize("response", [[], {}, {"items": [{"id": "4"}]}])
async def test_get_by_id_invalid_schema_is_sanitized(response):
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"response": response})),
        limiter=NoWaitLimiter(),
    ) as client:
        with pytest.raises(VKProtocolError) as e:
            await client.get_wall_post_by_id(
                -242100737, 4, access_token=TOKEN, account_id=UUID(int=1)
            )
    assert TOKEN not in str(e.value) and "items" not in str(e.value)


@pytest.mark.parametrize(
    "where,expected",
    [("suggests", "SUGGESTED"), ("all", "PUBLISHED"), ("direct", "EXISTS_UNKNOWN_PLACEMENT")],
)
def test_receipt_placement(where, expected):
    s = summary(item())
    assert (
        classify_receipt(
            [s] if where == "direct" else [],
            [s] if where == "suggests" else [],
            [s] if where == "all" else [],
        )
        == expected
    )


@pytest.mark.parametrize(
    "change,failed",
    [
        ({"text": "different"}, "marker_match"),
        (
            {"attachments": [{"type": "photo", "photo": {"owner_id": 615459987, "id": 457239318}}]},
            "audio_match",
        ),
        (
            {
                "attachments": [
                    {"type": "audio", "audio": {"owner_id": 2000410139, "id": 456245636}}
                ]
            },
            "photo_match",
        ),
    ],
)
def test_partial_mismatch_does_not_hide_receipt_evidence(change, failed):
    s = summary(item(**change))
    assert s[failed] is False and s["receipt_id_match"] is True
    assert classify_receipt([], [s], []) == "SUGGESTED"
    assert KEY not in str(s) and MARKER not in str(s)


def test_strong_suggestion_with_audio_omitted_and_different_receipt_id():
    s = summary(
        item(
            id=7, attachments=[{"type": "photo", "photo": {"owner_id": 615459987, "id": 457239318}}]
        )
    )
    assert not s["receipt_id_match"] and not s["audio_match"]
    assert classify_receipt([], [s], []) == "SUGGESTED"
    s["marker_match"] = False
    assert classify_receipt([], [s], []) == "UNKNOWN"


def test_no_evidence_or_conflicting_pages_is_unknown():
    s = summary(item())
    assert classify_receipt([], [], []) == "UNKNOWN"
    assert classify_receipt([], [s], [s]) == "UNKNOWN"


def test_live_rebased_community_photo_still_identifies_suggestion():
    s = summary(
        item(
            attachments=[
                {
                    "type": "photo",
                    "photo": {"owner_id": -242100737, "id": 456239018, "access_key": KEY},
                },
                {"type": "audio", "audio": {"owner_id": 2000410139, "id": 456245636}},
            ]
        )
    )
    assert all(
        s[k]
        for k in (
            "receipt_id_match",
            "owner_match",
            "from_user_match",
            "marker_match",
            "audio_match",
        )
    )
    assert not s["photo_match"]
    assert classify_receipt([s], [s], []) == "SUGGESTED"


async def test_get_by_id_uses_bounded_read_retry():
    attempts = []

    def handle(request):
        attempts.append(request.url.path)
        if len(attempts) == 1:
            return httpx.Response(503, text=TOKEN)
        return httpx.Response(200, json={"response": {"items": []}})

    async def no_wait(delay):
        pass

    async with VKClient(
        Settings(_env_file=None, vk_write_enabled=False),
        transport=httpx.MockTransport(handle),
        limiter=NoWaitLimiter(),
        sleeper=no_wait,
    ) as client:
        posts = await client.get_wall_post_by_id(
            -242100737, 4, access_token=TOKEN, account_id=UUID(int=1)
        )
    assert posts.items == [] and len(attempts) == 2


def test_summary_ignores_untrusted_attachment_fields():
    s = summary(
        item(
            attachments=[
                {"type": {"secret": KEY}, "text": TOKEN},
                {"type": "photo", "photo": {"owner_id": "secret", "id": 4, "access_key": KEY}},
            ]
        )
    )
    assert s["attachment_types"] == ["other", "photo"] and s["photos"] == []
    assert KEY not in str(s) and TOKEN not in str(s)
