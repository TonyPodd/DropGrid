"""Bounded wall date seeking and deterministic metadata-only age sampling."""

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from statistics import median
from typing import Protocol
from uuid import UUID

from dropgrid.db.models import CommunityReferencePhoto

ARCHIVE_STRATA = 4
ARCHIVE_SEEK_CALL_LIMIT = 48
ARCHIVE_SEEK_SAMPLE_SIZE = 5
ARCHIVE_SEEK_TOLERANCE = 25
ARCHIVE_MAX_WALL_OFFSET = 10_000_000


class WallPage(Protocol):
    count: int
    items: list[dict[str, object]]


def chronological_date(items: list[dict[str, object]]) -> float | None:
    dates = [
        float(date)
        for item in items
        if not item.get("is_pinned")
        and item.get("post_type") == "post"
        and type(date := item.get("date")) is int
        and 0 < date < 253402300799
    ]
    if any(a < b for a, b in zip(dates, dates[1:], strict=False)):
        return None
    return float(median(dates)) if dates else None


class WallDateSeeker:
    """Shared probe cache/budget for both boundaries; conservative overlap on scan."""

    def __init__(self, fetch: Callable[[int, int], Awaitable[WallPage]]) -> None:
        self.fetch = fetch
        self.calls = self.posts_inspected = 0
        self.cache: dict[int, tuple[float | None, bool]] = {}
        self.warnings: set[str] = set()

    async def probe(self, offset: int) -> tuple[float | None, bool]:
        if offset in self.cache:
            return self.cache[offset]
        if self.calls >= ARCHIVE_SEEK_CALL_LIMIT or offset > ARCHIVE_MAX_WALL_OFFSET:
            self.warnings.add("archive_seek_bound_reached")
            return None, False
        page = await self.fetch(offset, ARCHIVE_SEEK_SAMPLE_SIZE)
        self.calls += 1
        self.posts_inspected += len(page.items)
        value = chronological_date(page.items)
        empty = not page.items
        if not empty and value is None:
            self.warnings.add("archive_seek_chronology_unavailable")
        self.cache[offset] = value, empty
        return value, empty

    async def boundary(self, target: datetime) -> int | None:
        target_date = target.timestamp()
        value, empty = await self.probe(0)
        if empty or value is not None and value <= target_date:
            return 0
        low, high = 0, 100
        previous = value
        while self.calls < ARCHIVE_SEEK_CALL_LIMIT:
            value, empty = await self.probe(high)
            if value is not None and previous is not None and value > previous:
                self.warnings.add("archive_seek_unstable_chronology")
                return None
            if empty or value is not None and value <= target_date:
                break
            if value is None:
                # One all-pinned/deleted page cannot justify skipping history.
                return None
            low, high, previous = high, high * 2, value
        else:
            self.warnings.add("archive_seek_bound_reached")
            return None
        while high - low > ARCHIVE_SEEK_TOLERANCE:
            mid = (low + high) // 2
            value, empty = await self.probe(mid)
            if value is None and not empty:
                return None
            low_date = self.cache.get(low, (None, False))[0]
            high_date = self.cache.get(high, (None, False))[0]
            if value is not None and (
                low_date is not None
                and value > low_date
                or high_date is not None
                and value < high_date
            ):
                self.warnings.add("archive_seek_unstable_chronology")
                return None
            if empty or value is not None and value <= target_date:
                high = mid
            else:
                low = mid
        return low


def age_band(age: float, min_age: int, max_age: int) -> int:
    return min(
        ARCHIVE_STRATA - 1, max(0, int((age - min_age) * ARCHIVE_STRATA / (max_age - min_age)))
    )


def stratified_sample(
    rows: Sequence[CommunityReferencePhoto],
    community_id: UUID,
    min_age: int,
    max_age: int,
    now: datetime,
    limit: int,
) -> list[CommunityReferencePhoto]:
    bands: list[list[CommunityReferencePhoto]] = [[] for _ in range(ARCHIVE_STRATA)]
    # Hash ordering is stable in a daily bucket; window boundaries remain inclusive.
    for row in rows:
        age = (now - row.posted_at).total_seconds() / 86400
        if min_age <= age <= max_age:
            bands[age_band(age, min_age, max_age)].append(row)

    def key(row: CommunityReferencePhoto) -> tuple[bytes, int, int]:
        identity = f"{community_id}:{row.vk_photo_owner_id}_{row.vk_photo_id}:{now.date()}"
        return (
            hashlib.md5(identity.encode(), usedforsecurity=False).digest(),
            row.vk_photo_owner_id,
            row.vk_photo_id,
        )

    for band in bands:
        band.sort(key=key)
    result: list[CommunityReferencePhoto] = []
    # Round-robin allocates equally, redistributing sparse/empty band capacity.
    while len(result) < limit and any(bands):
        for band in bands:
            if band and len(result) < limit:
                result.append(band.pop(0))
    return result
