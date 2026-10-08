from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest

from dropgrid.config import Settings
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import VKInputError


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"type": "unknown_future_acceptance", "date": 1791483366}],
        [
            {
                "type": "wall",
                "feedback": {"id": 5, "from_id": -242100737},
                "parent": {"type": "unknown_parent", "feedback": {"id": 4}},
                "reply": {"id": 9},
            }
        ],
        [
            {
                "type": "wall_publish",
                "date": 1791483366,
                "feedback": {
                    "id": 5,
                    "owner_id": -242100737,
                    "attachments": [
                        {
                            "type": "photo",
                            "photo": {"id": 456239018, "owner_id": -242100737, "post_id": 4},
                        }
                    ],
                },
            }
        ],
    ],
)
async def test_notifications_read_only_and_optional_shapes(items):
    seen = []

    def handle(request):
        seen.append(request.url.path)
        assert request.url.path == "/method/notifications.get"
        form = parse_qs(request.content.decode())
        assert form["count"] == ["100"]
        assert form["start_time"] == ["1791480000"]
        assert form["end_time"] == ["1791484000"]
        if len(seen) == 1:
            assert "filters" not in form and "start_from" not in form
            return httpx.Response(
                200,
                json={
                    "response": {
                        "count": len(items),
                        "items": items,
                        "next_from": "opaque-cursor",
                        "profiles": [{"id": 615459987}],
                        "groups": [{"id": 242100737}],
                    }
                },
            )
        assert form["filters"] == ["wall"]
        assert form["start_from"] == ["opaque-cursor"]
        return httpx.Response(200, json={"response": {"count": 0, "items": []}})

    settings = Settings(_env_file=None, vk_write_enabled=False, vk_min_interval_seconds=0.34)
    async with VKClient(settings, transport=httpx.MockTransport(handle)) as client:
        first = await client.get_notifications(
            access_token="mock-token",
            account_id=UUID(int=1),
            start_time=1791480000,
            end_time=1791484000,
        )
        assert first.count == len(items)
        assert first.next_from == "opaque-cursor"
        assert [item.model_dump() for item in first.items] == [
            {"type": None, "date": None, "feedback": None, "parent": None, "reply": None, **item}
            for item in items
        ]
        assert first.profiles == [{"id": 615459987}]
        second = await client.get_notifications(
            access_token="mock-token",
            account_id=UUID(int=1),
            start_time=1791480000,
            end_time=1791484000,
            start_from=first.next_from,
            filters=["wall"],
        )
        assert second.items == [] and second.next_from is None
        with pytest.raises(VKInputError):
            await client.call(
                "notifications.markAsViewed", access_token="mock-token", account_id=UUID(int=1)
            )
    assert seen == ["/method/notifications.get"] * 2


@pytest.mark.parametrize(
    "params",
    [
        {"count": 0},
        {"count": 101},
        {"count": True},
        {"start_time": -1},
        {"end_time": True},
        {"start_time": 2, "end_time": 1},
        {"filters": ["unsupported"]},
        {"filters": []},
        {"start_from": ""},
    ],
)
async def test_invalid_notification_parameters_do_not_send(params):
    def handle(request):
        pytest.fail("Invalid input must not make a request")

    async with VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(VKInputError):
            await client.get_notifications(
                access_token="mock-token", account_id=UUID(int=1), **params
            )
