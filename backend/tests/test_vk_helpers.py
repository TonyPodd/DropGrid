import pytest
from pydantic import ValidationError

from dropgrid.config import Settings
from dropgrid.integrations.vk.errors import VKInputError
from dropgrid.integrations.vk.helpers import (
    build_suggested_post_request,
    parse_vk_audio_reference,
    post_contains_audio,
)
from dropgrid.integrations.vk.models import VKAttachment


@pytest.mark.parametrize(
    "value",
    [
        "https://vk.ru/audio474499203_456961224",
        "http://vk.com/audio474499203_456961224/",
        "vk.ru/audio474499203_456961224",
        "audio474499203_456961224",
        " HTTPS://VK.COM/audio474499203_456961224 ",
        "https://www.vk.com/audio474499203_456961224",
        "https://m.vk.ru/audio474499203_456961224",
        "https://vk.com/audio?z=audio474499203_456961224%2Fsome_album",
    ],
)
def test_audio_url(value):
    assert parse_vk_audio_reference(value) == VKAttachment("audio", 474499203, 456961224)


def test_negative_owner_and_access_key_roundtrip():
    reference = VKAttachment("audio", -123, 456, "known_access-key_1")
    assert parse_vk_audio_reference("https://vk.ru/" + reference.serialize()) == reference


@pytest.mark.parametrize(
    "value",
    [
        "",
        "foo",
        "https://evil.com/audio1_2",
        "https://vk.com.evil/audio1_2",
        "https://u:p@vk.com/audio1_2",
        "https://vk.com/audio1_2?access_token=secret",
        "https://vk.com/audio1_2#fragment",
        "audio0_2",
        "audio1_0",
        "audio1_-2",
        "audio--1_2",
        "audio1_2_extra!",
        "photo1_2",
        "ftp://vk.com/audio1_2",
        "https://vk.com/a/audio1_2",
        "audio9223372036854775808_2",
        "https://vk.com/audio?z=foo",
        "https://vk.com/audio?z=audio1_2&z=audio3_4",
    ],
)
def test_malformed_audio(value):
    with pytest.raises(VKInputError):
        parse_vk_audio_reference(value)


@pytest.mark.parametrize(
    ("attachment", "expected"),
    [
        (VKAttachment("photo", 1, 2), "photo1_2"),
        (VKAttachment("audio", -3, 4), "audio-3_4"),
        (VKAttachment("photo", -1, 2, "key"), "photo-1_2_key"),
    ],
)
def test_attachment_serialization(attachment, expected):
    assert attachment.serialize() == expected
    if attachment.access_key:
        assert attachment.access_key not in repr(attachment)


@pytest.mark.parametrize("caption", [None, "", "Caption"])
def test_suggest_payload(caption):
    params = build_suggested_post_request(
        123, caption=caption, photo=VKAttachment("photo", 1, 2), audio=VKAttachment("audio", -3, 4)
    )
    assert params["owner_id"] == -123 and params["from_group"] == 0
    assert params["attachments"] == "photo1_2,audio-3_4"
    assert params.get("message") == (caption or None)


@pytest.mark.parametrize("community_id", [0, -123, True, 2**63])
def test_invalid_suggest_target(community_id):
    with pytest.raises(VKInputError):
        build_suggested_post_request(
            community_id,
            caption=None,
            photo=VKAttachment("photo", 1, 2),
            audio=VKAttachment("audio", 1, 2),
        )


def test_match_nested_and_malformed():
    target = VKAttachment("audio", -3, 4)
    good = {"attachments": [{"type": "audio", "audio": {"owner_id": -3, "id": 4}}]}
    assert post_contains_audio(good, target)
    assert post_contains_audio({"copy_history": [{"copy_history": [good]}]}, target)
    assert post_contains_audio({"response": {"items": [good]}}, target)
    for bad in (
        None,
        [],
        {},
        {"attachments": None},
        {"attachments": [None, "audio-3_4"]},
        {"attachments": [{"type": "audio", "audio": {"owner_id": "-3", "id": 4}}]},
        {"attachments": [{"type": "photo", "audio": {"owner_id": -3, "id": 4}}]},
    ):
        assert not post_contains_audio(bad, target)
    assert not post_contains_audio(good, VKAttachment("audio", -3, 5))
    cycle = {}
    cycle["copy_history"] = [cycle]
    assert not post_contains_audio(cycle, target)


@pytest.mark.parametrize("value", ["*", "123,*", "-1", "0", "true", "1.2"])
def test_allowlist_no_wildcards(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, vk_test_allowed_community_ids=value)


def test_allowlist_and_defaults(monkeypatch):
    monkeypatch.setenv("VK_TEST_ALLOWED_COMMUNITY_IDS", "123, 456,123")
    settings = Settings(_env_file=None)
    assert settings.vk_test_allowed_community_ids == frozenset({123, 456})
    assert settings.vk_write_enabled is False
