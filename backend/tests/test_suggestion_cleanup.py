"""Explicit one-community cleanup diagnostic: same rule as the sender, guarded writes."""

from urllib.parse import parse_qs

import httpx
import pytest
from test_campaign_sender import GROUP, USER, NoWait, Tokens, seed, stale_suggestion
from test_campaign_sender import tracked_old_suggestion as tracked

from dropgrid.config import Settings
from dropgrid.db.models import Submission
from dropgrid.domain.enums import SubmissionStatus
from dropgrid.integrations.vk.cleanup_diagnostic import suggestion_cleanup
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import VKWriteDisabledError

pytestmark = pytest.mark.integration

SUGGESTIONS = [
    stale_suggestion(7, 6),
    stale_suggestion(8, 1),
    stale_suggestion(9, 6, from_id=USER + 1),
    stale_suggestion(11, 6),
]


def vk(calls, **settings):
    def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        params = parse_qs(request.content.decode())
        calls.append((method, params.get("post_id", [None])[0]))
        if method == "wall.get":
            return httpx.Response(
                200, json={"response": {"count": len(SUGGESTIONS), "items": SUGGESTIONS}}
            )
        if method == "wall.delete" and params["post_id"] == ["11"]:
            return httpx.Response(200, json={"error": {"error_code": 210}})
        return httpx.Response(200, json={"response": 1})

    return VKClient(
        Settings(_env_file=None, **settings),
        transport=httpx.MockTransport(handle),
        limiter=NoWait(),
    )


async def run(sessions, client, aid, dry_run):
    return await suggestion_cleanup(
        account_id=aid,
        community_id=GROUP,
        max_age_days=5,
        dry_run=dry_run,
        sessions=sessions,
        client=client,
        tokens=Tokens(),
    )


@pytest.mark.parametrize(
    "settings",
    [{}, {"vk_write_enabled": True}, {"vk_test_allowed_community_ids": frozenset({GROUP})}],
)
async def test_real_cleanup_needs_write_flag_and_target_before_any_request(
    sessions, tmp_path, settings
):
    data = await seed(sessions, tmp_path)
    calls = []
    async with vk(calls, **settings) as client:
        with pytest.raises(VKWriteDisabledError):
            await run(sessions, client, data[1], dry_run=False)
    assert calls == []


async def test_dry_run_only_lists_stale_own_suggestions(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    calls = []
    async with vk(calls) as client:
        report = await run(sessions, client, data[1], dry_run=True)
    assert report["stale"] == [7, 11] and report["deleted"] == []
    assert [method for method, _ in calls] == ["wall.get"]


async def test_cleanup_deletes_stale_own_suggestions_and_marks_tracked_ones(sessions, tmp_path):
    data = await seed(sessions, tmp_path)
    old = await tracked(sessions, data, 7)
    calls = []
    async with vk(
        calls, vk_write_enabled=True, vk_test_allowed_community_ids=frozenset({GROUP})
    ) as client:
        report = await run(sessions, client, data[1], dry_run=False)
    assert report["deleted"] == [7]
    assert report["errors"] == [{"post_id": 11, "error_code": 210}]
    assert [c for c in calls if c[0] == "wall.delete"] == [
        ("wall.delete", "7"),
        ("wall.delete", "11"),
    ]
    async with sessions() as db:
        row = await db.get(Submission, old)
        assert row.status == SubmissionStatus.not_found
        assert row.error_code == "suggestion_withdrawn"
