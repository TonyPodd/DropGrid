from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

import pytest
from photo_fixtures import candidate

from dropgrid.db.models import MediaAsset
from dropgrid.photos.domain import (
    Deduplicator,
    PhotoPolicy,
    PhotoQueryBuilder,
    PhotoRanker,
    PhotoSearch,
)
from dropgrid.photos.planner import assign_assets


@pytest.mark.parametrize(
    "category",
    [
        "ГРУЗОВИКИ",
        "тракторы",
        "авто",
        "автомобили",
        "мото",
        "мотоциклы",
        "природа",
        "животные",
        "собаки",
        "кошки",
        "знакомства",
        "любовь",
        "цитаты",
    ],
)
def test_query_plans(category):
    plan = PhotoQueryBuilder().build(category)
    assert [q.lang for q in plan.variants] == ["ru", "en", "en"]
    assert all(
        q.orientation == "all" and q.safesearch and q.image_type == "photo" for q in plan.variants
    )
    assert all(q.min_width == 850 and q.per_page == 75 and q.page == 1 for q in plan.variants)


def test_normalization_unknown_and_sensitive():
    builder = PhotoQueryBuilder()
    assert builder.build("  ГРУЗОВИКИ \n") == builder.build("грузовики")
    assert builder.build(" СВЕТ  И   ТЕНИ ").variants[0].query == "свет и тени"
    assert builder.build("space").variants[0].lang == "en"
    assert builder.build(None).variants[0].query == "природа"
    plan = builder.build("dating")
    assert plan.sensitive
    assert not any(
        term in q.query.split()
        for q in plan.variants
        for term in ("portrait", "person", "woman", "man", "портрет")
    )


@pytest.mark.parametrize(
    "tag",
    [
        "portrait",
        "woman",
        "man",
        "girl",
        "boy",
        "model",
        "face",
        "selfie",
        "person",
        "портрет",
        "женщина",
        "мужчина",
        "девушка",
        "человек",
    ],
)
def test_sensitive_people_metadata(tag):
    assert not PhotoPolicy().candidate_allowed(candidate(tags=(tag,)), sensitive=True)
    assert PhotoPolicy().candidate_allowed(candidate(tags=("flowers",)), sensitive=True)


def test_ranker_signals_are_stable():
    ranker, query = PhotoRanker(), PhotoSearch(query="truck highway", lang="en")
    base = candidate()
    score = ranker.score(base, query)
    assert score == ranker.score(base, query)
    assert score > ranker.score(candidate(tags=("cat",)), query)
    assert score > ranker.score(base, query, priority=1)
    assert score > ranker.score(base, query, usage=3)
    assert score > ranker.score(base, query, pending=3)
    assert score > ranker.score(base, query, creator_selected=3)
    assert score > ranker.score(base, query, last_used_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert ranker.score(candidate(width=2000, height=2000), query) > score
    assert ranker.score(candidate(likes=100, views=10000, downloads=10000), query) > score
    assert ranker.score(candidate(width=2000, height=1000), query) < ranker.score(
        candidate(width=1500, height=1000), query
    )


def test_hash_hamming():
    dedup = Deduplicator(4)
    assert dedup.near("0000000000000000", "000000000000000f")
    assert not dedup.near("0000000000000000", "ffffffffffffffff")
    assert not dedup.near(None, "123")


def test_sensitive_license_restriction_even_without_people_tags():
    assert not PhotoPolicy().candidate_allowed(
        candidate(tags=("flowers",), license_code="pixabay-content-license"), sensitive=True
    )


def test_balanced_unique_assignment_and_usage_penalty():
    assets = [
        MediaAsset(
            id=UUID(int=i),
            storage_key="x",
            width=1000,
            height=1000,
            creator_name=str(i),
            usage_count=0,
            last_used_at=None,
        )
        for i in range(1, 4)
    ]
    submissions = [UUID(int=i) for i in range(10, 21)]
    result = assign_assets(submissions, assets, Counter(), 3, Counter())
    assert len(result) == 9
    assert len(set(list(result.values())[:3])) == 3
    assert set(Counter(result.values()).values()) == {3}
    assert all(
        a != b for a, b in zip(list(result.values()), list(result.values())[1:], strict=False)
    )
    assert result == assign_assets(submissions, list(reversed(assets)), Counter(), 3, Counter())
    assets[0].usage_count = 100
    assert (
        next(iter(assign_assets(submissions[:1], assets, Counter(), 3, Counter()).values()))
        != assets[0].id
    )
