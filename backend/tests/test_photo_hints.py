import pytest
from photo_fixtures import candidate

from dropgrid.photos.concepts import concept_queries
from dropgrid.photos.domain import PhotoPolicy, PhotoQueryBuilder
from dropgrid.photos.retrieval import retrieve_photos


@pytest.mark.parametrize(
    ("hint", "query"),
    [
        ("девушка с машиной", "woman car"),
        ("дембель", "soldier"),
        ("еда в казане", "food cauldron"),
        ("БМВ Е60", "bmw e60"),
        ("БМВ Е38", "bmw e38"),
        ("W201", "mercedes w201"),
    ],
)
def test_explicit_mapping(hint, query):
    assert concept_queries(hint)[0] == query


@pytest.mark.parametrize("hint", ["Д-П", "П-Д", "П", "Д", "ДС", "неизвестная абракадабра"])
def test_unknown_hint_category_fallback(hint):
    builder = PhotoQueryBuilder()
    assert builder.build("МУЗЫКА", hint).variants == builder.build("МУЗЫКА").variants


def test_bounded_queries_desired_separate_and_no_negative_syntax():
    p = PhotoQueryBuilder().build("МУЗЫКА", "девушка с машиной " * 1000, "закат ночь город " * 1000)
    queries = [q.query for q in p.variants]
    assert len(queries) <= 4 and len(set(queries)) == len(queries)
    assert "woman car" in queries and "girl car" in queries
    assert any("sunset" in q for q in queries)
    assert all(len(q.query) <= 100 and q.per_page == 24 and q.page == 1 for q in p.variants)


async def test_multi_query_bounds_dedup_provenance_and_fair_quota():
    class Cache:
        class Provider:
            supports_sensitive_context = True

        provider = Provider()
        calls = []

        async def search(self, search):
            self.calls.append(search)
            return [
                candidate(provider_asset_id="000-same"),
                candidate(provider_asset_id="001-" + search.query),
                *[candidate(provider_asset_id=f"{search.query}-{i}") for i in range(100)],
            ], False

    cache = Cache()
    plan = PhotoQueryBuilder().build("МУЗЫКА", "девушка с машиной")
    result = await retrieve_photos(cache, plan, PhotoPolicy())
    assert len(cache.calls) <= 4 and result.requests == len(cache.calls)
    assert len(result.items) <= 96
    identities = [i.photo.provider_asset_id for i in result.items]
    assert len(identities) == len(set(identities))
    duplicate = next(i for i in result.items if i.photo.provider_asset_id == "000-same")
    assert duplicate.queries == [s.query for s in cache.calls]
    assert len({q for item in result.items[:8] for q in item.queries}) == len(cache.calls)


async def test_clip_cpu_concurrency_remains_one(tmp_path, monkeypatch):
    import asyncio
    import time

    from dropgrid.photos.visual import OnnxCLIPEmbedder, VisualEmbedding

    embedder = OnnxCLIPEmbedder(tmp_path / "unused")
    current = peak = 0

    def embed(data):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        time.sleep(0.01)
        current -= 1
        return VisualEmbedding(embedder.model, embedder.dimensions, (1.0,) * embedder.dimensions)

    monkeypatch.setattr(embedder, "_embed", embed)
    await asyncio.gather(*(embedder.embed_image(b"fixture") for _ in range(6)))
    assert peak == 1
