from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from dropgrid.config import Settings
from dropgrid.integrations.vk.client import VKClient


def groups(items):
    return httpx.Response(200, json={"response": {"groups": items}})


@pytest.mark.parametrize(
    "refs", [["vk.com/a.b", "14vk14", "club3", "public4"], ["a.b", "14vk14", "3", "4"]]
)
async def test_batch_correlates_identity_not_order(refs):
    calls = []

    def handler(r):
        form = parse_qs(r.content.decode())
        calls.append(form)
        return groups(
            [
                {"id": 4, "screen_name": "renamed_four"},
                {"id": 2, "screen_name": "14vk14"},
                {"id": 3, "screen_name": "renamed_three"},
                {"id": 1, "screen_name": "a.b"},
            ]
        )

    async with VKClient(
        Settings(_env_file=None, vk_min_interval_seconds=0.34),
        transport=httpx.MockTransport(handler),
    ) as c:
        result = await c.resolve_communities(
            refs, access_token=SecretStr("mock"), account_id=uuid4()
        )
    assert [x.group.id for x in result] == [1, 2, 3, 4]
    assert len(calls) == 1 and "group_ids" in calls[0] and "group_id" not in calls[0]


async def test_missing_and_renamed_alias_singleton_fallback():
    calls = []

    def handler(r):
        f = parse_qs(r.content.decode())
        calls.append(f)
        if "group_ids" in f:
            return groups(
                [{"id": 8, "screen_name": "new_alias"}, {"id": 1, "screen_name": "known"}]
            )
        return groups(
            [{"id": 8, "screen_name": "new_alias"}] if f["group_id"] == ["old_alias"] else []
        )

    async with VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handler)) as c:
        result = await c.resolve_communities(
            ["old_alias", "known", "missing"], access_token="mock", account_id=uuid4()
        )
    assert [x.status for x in result] == ["resolved", "resolved", "not_found"]
    assert result[0].group.id == 8
    assert len(calls) == 3


async def test_large_input_chunking_521():
    sizes = []

    def handler(r):
        ids = parse_qs(r.content.decode())["group_ids"][0].split(",")
        sizes.append(len(ids))
        return groups([{"id": int(x.removeprefix("club"))} for x in reversed(ids)])

    async with VKClient(
        Settings(_env_file=None), transport=httpx.MockTransport(handler), sleeper=lambda _: noop()
    ) as c:
        from unittest.mock import AsyncMock

        c.limiter.acquire = AsyncMock()
        result = await c.resolve_communities(
            [f"club{i}" for i in range(1, 522)], access_token="mock", account_id=uuid4()
        )
    assert len(result) == 521 and all(x.status == "resolved" for x in result)
    assert sizes == [25] * 20 + [21]


async def noop():
    pass


async def test_transient_batch_does_not_fan_out():
    calls = []

    def handler(r):
        calls.append(parse_qs(r.content.decode()))
        return httpx.Response(200, json={"error": {"error_code": 10}})

    async with VKClient(
        Settings(_env_file=None), transport=httpx.MockTransport(handler), sleeper=lambda _: noop()
    ) as c:
        result = await c.resolve_communities(["a", "b"], access_token="mock", account_id=uuid4())
    assert all(x.status == "transient_error" for x in result)
    assert len(calls) == 3 and all("group_ids" in x for x in calls)


async def test_typed_deactivated_private():
    async with VKClient(
        Settings(_env_file=None),
        transport=httpx.MockTransport(
            lambda r: groups(
                [
                    {"id": 1, "deactivated": "banned"},
                    {"id": 2, "is_closed": 1},
                    {"id": 3, "is_closed": 1, "is_member": 1},
                ]
            )
        ),
    ) as c:
        result = await c.resolve_communities(
            ["club1", "public2", "club3"], access_token="mock", account_id=uuid4()
        )
    assert [x.status for x in result] == ["deactivated", "private_or_unavailable", "resolved"]


async def test_read_concurrency_is_bounded_separately_from_limiter():
    import asyncio
    from unittest.mock import AsyncMock

    current = peak = 0

    async def handler(r):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.01)
        current -= 1
        return httpx.Response(200, json={"response": [{"id": 1}]})

    async with VKClient(
        Settings(_env_file=None, vk_read_concurrency=1), transport=httpx.MockTransport(handler)
    ) as c:
        c.limiter.acquire = AsyncMock()
        await asyncio.gather(
            *(c.get_current_user(access_token="mock", account_id=uuid4()) for _ in range(5))
        )
    assert peak == 1


async def test_authentication_failure_stops_batch_without_fallback():
    from dropgrid.integrations.vk.errors import VKAuthenticationError

    seen = []

    def handler(r):
        seen.append(r)
        return httpx.Response(200, json={"error": {"error_code": 5}})

    async with VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(VKAuthenticationError):
            await c.resolve_communities(["a", "b"], access_token="mock", account_id=uuid4())
    assert len(seen) == 1


async def test_numeric_singleton_mismatch_never_associates_wrong_group():
    def handler(r):
        p = parse_qs(r.content.decode())
        return groups([] if "group_ids" in p else [{"id": 999}])

    async with VKClient(Settings(_env_file=None), transport=httpx.MockTransport(handler)) as c:
        result = await c.resolve_communities(["club123"], access_token="mock", account_id=uuid4())
    assert result[0].status == "transient_error" and result[0].group is None
