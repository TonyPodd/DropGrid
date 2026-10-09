from uuid import UUID

import pytest

from dropgrid.db.models import CommunityReferencePhoto
from dropgrid.photos.density import assess_references
from dropgrid.photos.domain import PhotoQueryBuilder
from dropgrid.photos.visual import FakeVisualEmbedder, VisualEmbedding, serialize_embedding


def reference(i, vector):
    embedder = FakeVisualEmbedder()
    return CommunityReferencePhoto(
        id=UUID(int=i),
        enabled=True,
        is_style_reference=True,
        vk_photo_owner_id=-1,
        vk_photo_id=i,
        sha256=f"{i:064x}",
        perceptual_hash=None,
        embedding=serialize_embedding(VisualEmbedding(embedder.model, 3, vector)),
        embedding_model=embedder.model,
        embedding_dimensions=3,
    )


def test_density_excludes_self_and_preserves_small_pool():
    rows = [reference(1, (1, 0, 0)), reference(2, (0, 1, 0))]
    values, _ = assess_references(rows, FakeVisualEmbedder())
    assert all(v.role == "core" and v.density == 0 and v.nearest == 0 for v in values)


def test_relative_density_collapses_duplicate_and_selects_core():
    rows = [reference(i, (1, 0.01 * i, 0)) for i in range(1, 9)]
    rows += [reference(9, (0, 0, 1)), reference(10, (0, 1, 0))]
    duplicate = reference(11, (1, 0, 0))
    duplicate.sha256 = rows[0].sha256
    rows.append(duplicate)
    values, _ = assess_references(rows, FakeVisualEmbedder())
    assert sum(v.role == "core" for v in values) == 8
    assert values[-1].duplicate and values[-1].role == "auxiliary"
    assert values[8].role == values[9].role == "auxiliary"
    assert values == assess_references(list(reversed(rows)), FakeVisualEmbedder())[0][::-1]


@pytest.mark.parametrize(
    ("category", "hint", "expected"),
    [
        ("ХОНДА АККОРД", None, "honda accord"),
        ("БМВ", "БМВ Е60", "bmw e60"),
        ("БМВ Е38", None, "bmw e38"),
        ("МЕРСЕДЕС", "W201", "mercedes w201"),
    ],
)
def test_car_queries(category, hint, expected):
    queries = PhotoQueryBuilder().build(category, hint).variants
    assert [q.query for q in queries] == [
        expected + suffix for suffix in ("", " car", " aesthetic", " street")
    ]
    assert all(q.lang == "en" for q in queries)
