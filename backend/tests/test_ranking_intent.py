from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from photo_fixtures import candidate

from dropgrid.photos.concepts import retrieval_hint
from dropgrid.photos.domain import PhotoQueryBuilder
from dropgrid.photos.pool import PoolCandidate, rank_pool


async def test_unified_music_pool_preserves_woman_car_intent(monkeypatch):
    async def no_usage(*args):
        return []

    async def no_profile(*args):
        return None

    async def no_references(*args):
        return [], None

    @asynccontextmanager
    async def session():
        yield SimpleNamespace(get=no_profile)

    monkeypatch.setattr("dropgrid.photos.pool.community_usage", no_usage)
    visual = SimpleNamespace(sessions=session, reference_rows=no_references, embedder=None)
    plan = PhotoQueryBuilder().build("МУЗЫКА", retrieval_hint(None, None, "девушка с машиной"))
    pool = [
        PoolCandidate(
            candidate(provider_asset_id="piano", tags=("музыка", "piano", "music")), "library"
        ),
        PoolCandidate(
            candidate(provider_asset_id="violin", tags=("музыка", "violin", "music")), "library"
        ),
        PoolCandidate(candidate(provider_asset_id="woman", tags=("woman", "car")), "pinterest"),
    ]
    ranked = await rank_pool(visual, uuid4(), pool, plan)
    assert ranked[0].photo.provider_asset_id == "woman"
    assert ranked[0].score.final_score > ranked[1].score.final_score
    assert [q.query for q in plan.ranking_variants] == ["woman car", "girl car"]
