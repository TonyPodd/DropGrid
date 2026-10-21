from photo_fixtures import candidate

from dropgrid.db.models import (
    Campaign,
    Community,
    Grid,
    MediaAsset,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
)
from dropgrid.domain.enums import CampaignStatus
from dropgrid.photos.domain import PhotoPolicy, PhotoQueryBuilder, PhotoSearch
from dropgrid.photos.history import ProposedPhotos, proposed_photos
from dropgrid.photos.pinterest import PinterestPhotoProvider
from dropgrid.photos.pool import PoolCandidate
from dropgrid.photos.retrieval import retrieve_photos


def pin_row(i: int) -> dict[str, object]:
    identity = str(123450000 + i)
    return {
        "id": identity,
        "url": f"https://www.pinterest.com/pin/{identity}/",
        "imageUrl": f"https://i.pinimg.com/originals/{i}.jpg",
        "width": 1000,
        "height": 1000,
        "title": "Honda Accord car",
    }


def asset(n: int) -> MediaAsset:
    return MediaAsset(
        storage_key=f"history/{n}.jpg",
        sha256=f"{n:064x}",
        perceptual_hash=f"{n:016x}",
        provider="pinterest",
        provider_asset_id=f"pin-{n}",
        width=800,
        height=800,
        mime_type="image/jpeg",
    )


async def test_proposed_photos_cover_picks_choices_and_other_campaigns(sessions):
    async with sessions() as db, db.begin():
        grid = Grid(name="History")
        community = Community(domain="history1")
        assets = [asset(n) for n in range(1, 6)]
        db.add_all([grid, community, *assets])
        await db.flush()
        earlier, current = (
            Campaign(
                name=name,
                grid_id=grid.id,
                track_url="https://vk.ru/audio1_2",
                status=CampaignStatus.ready,
            )
            for name in ("Earlier", "Current")
        )
        db.add_all([earlier, current])
        await db.flush()
        old = Submission(
            campaign_id=earlier.id, community_id=community.id, media_asset_id=assets[3].id
        )
        mine = Submission(
            campaign_id=current.id, community_id=community.id, media_asset_id=assets[4].id
        )
        db.add_all([old, mine])
        await db.flush()
        session = PhotoSelectionSession(
            campaign_id=earlier.id,
            submission_id=old.id,
            community_id=community.id,
            baseline_rank=1,
            proposed_rank=1,
        )
        db.add(session)
        await db.flush()
        for rank, (item, selected) in enumerate(
            [(assets[0], False), (assets[1], False), (assets[2], True)], 1
        ):
            db.add(
                PhotoSelectionCandidate(
                    selection_session_id=session.id,
                    rank=rank,
                    provider="pinterest",
                    source_identity=item.provider_asset_id,
                    media_asset_id=item.id,
                    features={},
                    candidate={
                        "provider": "pinterest",
                        "provider_asset_id": item.provider_asset_id,
                    },
                    selected=selected,
                )
            )
        current_id = current.id
    async with sessions() as db:
        seen = await proposed_photos(db, current_id)
    # The bot's pick, the operator's choice and another campaign's assignment.
    assert {("pinterest", "pin-1"), ("pinterest", "pin-3"), ("pinterest", "pin-4")} <= (
        seen.identities
    )
    # A merely displayed alternative and this campaign's own assignment stay available.
    assert ("pinterest", "pin-2") not in seen.identities
    assert ("pinterest", "pin-5") not in seen.identities
    assert f"{1:064x}" in seen.sha256 and f"{2:064x}" not in seen.sha256


def test_matching_by_identity_hashes_and_assets():
    seen = ProposedPhotos()
    seen.remember(("pinterest", "1"), sha256="a" * 64, perceptual_hash="f" * 16)
    assert (
        PoolCandidate(candidate(provider="pinterest", provider_asset_id="1"), "pinterest") in seen
    )
    same_bytes = PoolCandidate(
        candidate(provider="pinterest", provider_asset_id="2"), "pinterest", sha256="a" * 64
    )
    same_look = PoolCandidate(
        candidate(provider="pinterest", provider_asset_id="3"),
        "pinterest",
        perceptual_hash="f" * 16,
    )
    fresh = PoolCandidate(candidate(provider="pinterest", provider_asset_id="4"), "pinterest")
    assert same_bytes in seen and same_look in seen and fresh not in seen
    # Library assets without provider identity never match each other by the empty identity.
    seen.remember(("", ""))
    assert PoolCandidate(candidate(provider="", provider_asset_id=""), "library") not in seen


class PagedCache:
    class Provider:
        supports_sensitive_context = True

    provider = Provider()

    def __init__(self):
        self.calls: list[PhotoSearch] = []

    async def search(self, search):
        self.calls.append(search)
        return [
            candidate(provider_asset_id=f"{search.query}-p{search.page}-{i}") for i in range(10)
        ], False


async def test_retrieval_searches_next_page_when_photos_were_proposed():
    plan = PhotoQueryBuilder().build("МУЗЫКА", "девушка с машиной")
    cache = PagedCache()
    plain = await retrieve_photos(cache, plan, PhotoPolicy())
    assert {s.page for s in cache.calls} == {1}

    cache = PagedCache()
    result = await retrieve_photos(
        cache, plan, PhotoPolicy(), exclude=lambda photo: "-p1-" in photo.provider_asset_id
    )
    assert {s.page for s in cache.calls} == {1, 2}
    assert result.items and all("-p2-" in i.photo.provider_asset_id for i in result.items)
    assert len(plain.items) == len(result.items)


async def test_retrieval_stays_on_first_page_when_enough_fresh_photos():
    plan = PhotoQueryBuilder().build("МУЗЫКА", "девушка с машиной")
    cache = PagedCache()
    await retrieve_photos(
        cache,
        plan,
        PhotoPolicy(),
        target=5,
        exclude=lambda photo: photo.provider_asset_id.endswith("-0"),
    )
    assert {s.page for s in cache.calls} == {1}


async def test_pinterest_second_page_returns_next_pins():
    class Backend:
        limits: list[int] = []

        async def search(self, query, limit):
            self.limits.append(limit)
            return [pin_row(i) for i in range(1, limit + 1)]

    backend = Backend()
    provider = PinterestPhotoProvider(backend)
    first = await provider.search(PhotoSearch(query="Honda Accord"))
    second = await provider.search(PhotoSearch(query="Honda Accord", page=2))
    assert backend.limits == [25, 50]
    first_ids = [c.provider_asset_id for c in first.candidates]
    second_ids = [c.provider_asset_id for c in second.candidates]
    assert len(first_ids) == len(second_ids) == 25 and not set(first_ids) & set(second_ids)
    assert second.candidates[0].provider_position == 25


async def test_planner_never_proposes_the_same_photo_again(sessions, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy import select
    from test_archive_integration import cached_reference
    from test_readiness_workflow import archive_fixture

    from dropgrid.db.models import CommunityReferencePhoto
    from dropgrid.photos.schemas import MediaPlanInput
    from dropgrid.photos.visual import FakeVisualEmbedder, VisualEmbedding, serialize_embedding
    from dropgrid.photos.visual_library import VisualLibrary

    cid, sid, target, ref, _, provider, planner, _, http = await archive_fixture(sessions, tmp_path)
    embedder = FakeVisualEmbedder()
    target_ref, _ = await cached_reference(
        sessions,
        provider.collector.storage,
        embedder,
        target,
        photo_id=9,
        style=True,
        age=1,
        seed=9,
    )
    vector = VisualEmbedding(embedder.model, 3, (1, 0, 0))
    async with sessions() as db, db.begin():
        for rid in (ref.id, target_ref.id):
            (await db.get(CommunityReferencePhoto, rid)).embedding = serialize_embedding(vector)
    async with sessions() as db:
        ref = await db.get(CommunityReferencePhoto, ref.id)
    provider.prepare = AsyncMock(return_value=ref)
    provider.collector.client = SimpleNamespace(settings=planner.settings)
    planner.visual = VisualLibrary(sessions, planner.storage, embedder)
    assert (await planner.plan(cid, MediaPlanInput())).newly_assigned == 1
    async with sessions() as db, db.begin():
        current = await db.get(Campaign, cid)
        rerun = Campaign(
            name="Next run",
            grid_id=current.grid_id,
            track_url=current.track_url,
            status=CampaignStatus.ready,
        )
        db.add(rerun)
        await db.flush()
        db.add(Submission(campaign_id=rerun.id, community_id=target))
        rerun_id = rerun.id
    # The only candidate was proposed in the first run: it is not offered again.
    report = await planner.plan(rerun_id, MediaPlanInput())
    assert report.newly_assigned == 0 and report.unassigned == 1
    async with sessions() as db:
        row = await db.scalar(select(Submission).where(Submission.campaign_id == rerun_id))
        assert row.media_asset_id is None and "repeats_exhausted" in row.photo_attention
    await http.aclose()


async def test_alternatives_hide_photos_given_to_other_communities(sessions):
    from sqlalchemy import select

    from dropgrid.photos.review import snapshot
    from dropgrid.photos.visual import RankedPhoto

    async with sessions() as db, db.begin():
        grid = Grid(name="Alternatives")
        community = Community(domain="alternatives1")
        chosen, other, fresh = asset(11), asset(12), asset(13)
        db.add_all([grid, community, chosen, other, fresh])
        await db.flush()
        campaign = Campaign(
            name="Run", grid_id=grid.id, track_url="https://vk.ru/audio1_2", status="ready"
        )
        db.add(campaign)
        await db.flush()
        submission = Submission(
            campaign_id=campaign.id, community_id=community.id, media_asset_id=chosen.id
        )
        db.add(submission)
        await db.flush()
        items = [
            PoolCandidate(
                candidate(provider="pinterest", provider_asset_id=a.provider_asset_id),
                "pinterest",
                asset=a,
                score=RankedPhoto(1, None, score, None, 0),
            )
            for a, score in ((other, 0.9), (chosen, 0.8), (fresh, 0.7))
        ]
        taken = ProposedPhotos()
        taken.remember_asset(other)
        taken.remember_asset(chosen)
        plan = PhotoQueryBuilder().build("truck")
        await snapshot(db, submission, plan, items, items, "deterministic-v1", [], hidden=taken)
        shown = (
            await db.scalars(
                select(PhotoSelectionCandidate.source_identity).order_by(
                    PhotoSelectionCandidate.rank
                )
            )
        ).all()
    # The photo given to another community is not offered; the own pick stays.
    assert shown == ["pin-11", "pin-13"]
