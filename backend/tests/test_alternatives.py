from collections import Counter

from photo_fixtures import candidate

from dropgrid.photos.pool import PoolCandidate
from dropgrid.photos.review import DISPLAY_LIMIT, display_set


def pool(source, count, start):
    return [
        PoolCandidate(candidate(provider=source, provider_asset_id=f"{source}-{i}"), source)
        for i in range(start, start + count)
    ]


def test_alternatives_keep_room_for_pinterest_and_own_old_photos():
    # Ranked best first: other publics' photos outscore every pin and own old photo.
    items = (
        pool("vk_category_archive", 30, 0)
        + pool("library", 5, 0)
        + pool("pinterest", 12, 0)
        + pool("vk_archive", 12, 0)
    )
    chosen = items[0]
    shown = display_set(items, chosen)
    sources = Counter(item.source for item in shown)
    assert len(shown) == DISPLAY_LIMIT == 24 and chosen in shown
    assert sources["pinterest"] == 8 and sources["vk_archive"] == 8
    # Order stays the ranking order, so the production pick is unchanged.
    assert shown == [item for item in items if item in shown]


def test_small_pools_are_shown_whole():
    items = pool("vk_category_archive", 3, 0) + pool("pinterest", 2, 0)
    assert display_set(items, items[0]) == items
