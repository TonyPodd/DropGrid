from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from dropgrid.db.models import CommunityReferencePhoto
from dropgrid.integrations.vk.models import WallPosts
from dropgrid.photos.archive_retrieval import (
    ARCHIVE_SEEK_CALL_LIMIT,
    WallDateSeeker,
    age_band,
    chronological_date,
    stratified_sample,
)

NOW = datetime(2026, 10, 9, tzinfo=UTC)
CID = UUID(int=1)


def row(identity, age):
    return CommunityReferencePhoto(
        community_id=CID,
        vk_photo_owner_id=-123,
        vk_photo_id=identity,
        posted_at=NOW - timedelta(days=age),
    )


def test_strata_determinism_boundaries_sparse_redistribution():
    rows = [row(i + 1, age) for i, age in enumerate([179, 180, 270, 360, 450, 540, 541])]
    picked = stratified_sample(rows, CID, 180, 540, NOW, 28)
    assert {r.vk_photo_id for r in picked} == {2, 3, 4, 5, 6}
    assert picked == stratified_sample(list(reversed(rows)), CID, 180, 540, NOW, 28)
    dense = [row(i + 100, 181 + i / 10) for i in range(100)] + [
        row(1, 300),
        row(2, 400),
        row(3, 530),
    ]
    sampled = stratified_sample(dense, CID, 180, 540, NOW, 28)
    assert len(sampled) == 28
    assert {1, 2, 3} <= {r.vk_photo_id for r in sampled}
    assert len({age_band((NOW - r.posted_at).days, 180, 540) for r in sampled}) == 4
    assert any(r.posted_at < NOW - timedelta(days=365) for r in sampled)


@pytest.mark.parametrize("pinned", [False, True])
async def test_exponential_binary_seek_skips_thousands(pinned):
    offsets = []

    async def fetch(offset, count):
        offsets.append(offset)
        items = [
            dict(date=int((NOW - timedelta(hours=i)).timestamp()), post_type="post")
            for i in range(offset, min(offset + count, 20000))
        ]
        if pinned and items:
            items[0] = dict(date=1, post_type="post", is_pinned=1)
        return WallPosts(count=20000, items=items)

    seeker = WallDateSeeker(fetch)
    upper = await seeker.boundary(NOW - timedelta(days=180))
    lower = await seeker.boundary(NOW - timedelta(days=540))
    assert upper is not None and 4250 < upper < 4320
    assert lower is not None and 12900 < lower < 12960
    assert offsets[:7] == [0, 100, 200, 400, 800, 1600, 3200]
    assert any(
        offset not in {0, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600} for offset in offsets
    )
    assert seeker.calls <= ARCHIVE_SEEK_CALL_LIMIT
    assert seeker.posts_inspected <= ARCHIVE_SEEK_CALL_LIMIT * 5


@pytest.mark.parametrize("kind", ["empty", "malformed", "unstable", "budget"])
async def test_seek_safe_termination(kind):
    async def fetch(offset, count):
        if kind == "empty":
            return WallPosts(count=99999, items=[])
        if kind == "malformed":
            return WallPosts(count=1000, items=[dict(date="wrong", post_type="post")])
        date = int(NOW.timestamp()) + (offset if kind == "unstable" else 0)
        return WallPosts(count=99999999, items=[dict(date=date, post_type="post")])

    seeker = WallDateSeeker(fetch)
    if kind == "budget":
        seeker.calls = ARCHIVE_SEEK_CALL_LIMIT - 2
    result = await seeker.boundary(NOW - timedelta(days=180))
    assert result == 0 if kind == "empty" else result is None
    assert seeker.calls <= ARCHIVE_SEEK_CALL_LIMIT
    if kind != "empty":
        assert seeker.warnings


def test_pinned_and_malformed_ignored():
    assert (
        chronological_date(
            [
                dict(date=1, post_type="post", is_pinned=1),
                dict(date=102, post_type="post"),
                dict(date=100, post_type="post"),
                dict(date=10**100, post_type="post"),
                dict(date=True, post_type="post"),
            ]
        )
        == 101
    )
