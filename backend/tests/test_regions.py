from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from photo_fixtures import candidate

from dropgrid.db.models import (
    Community,
    CommunityContentProfile,
    CommunityReferencePhoto,
    Grid,
    GridCommunity,
    utcnow,
)
from dropgrid.photos.category_archive import VKCategoryArchivePhotoProvider
from dropgrid.photos.domain import PhotoQueryBuilder
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.pool import PoolCandidate, rank_pool
from dropgrid.photos.regions import parse_region, region_evidence
from dropgrid.photos.visual import FakeVisualEmbedder, VisualEmbedding, serialize_embedding
from dropgrid.photos.visual_library import VisualLibrary


@pytest.mark.parametrize(
    "label",
    [
        "24 регион",
        "24region",
        "24 REGION",
        "регион 24",
        "24/124 регион",
        "24 124 регион",
        "  24   регион ",
        "124 регион",
    ],
)
def test_explicit_aliases(label):
    r = parse_region(label)
    assert r.region_known and r.region_key == "krasnoyarsk_krai"
    assert r.vehicle_codes == ("24", "124")
    assert "Красноярск" in r.search_places


@pytest.mark.parametrize(
    "label", ["BMW E24", "ВАЗ 2114", "24", "группа 54", "music 124", "24 машины", "Honda Accord 7"]
)
def test_numbers_are_not_regions(label):
    assert parse_region(label) is None


def test_unknown_and_conflicting_codes_never_guessed():
    for label in ["999 регион", "24/54 регион"]:
        r = parse_region(label)
        assert r.recognized_region and not r.region_known and r.region_key is None
    assert region_evidence("BMW Club 24 club24") is None
    assert region_evidence("Автомобили Красноярск").region_key == "krasnoyarsk_krai"
    assert region_evidence("Красноярск Новосибирск") is None


def test_regional_queries_and_unverified_pinterest():
    plan = PhotoQueryBuilder().build("24 регион")
    assert plan.region_context.region_key == "krasnoyarsk_krai"
    assert all("24" not in q.query for q in plan.variants)
    assert any("krasnoyarsk" in q.query for q in plan.variants)
    item = PoolCandidate(candidate(1, provider="pinterest"), "pinterest")
    assert item.region_status == "unverified" and item.region_key is None


@pytest.mark.integration
async def test_same_region_shortlist_not_cross_region_even_with_better_embedding(
    sessions, tmp_path
):
    embedder = FakeVisualEmbedder()
    visual = VisualLibrary(sessions, LocalMediaStorage(tmp_path), embedder)
    async with sessions() as db, db.begin():
        grid = Grid(name="Region fixture")
        communities = [Community(domain=d) for d in ("target", "same", "other")]
        db.add_all([grid, *communities])
        await db.flush()
        for c, category, i, vector in zip(
            communities,
            ["24 регион", "124 регион", "54 регион"],
            [1, 2, 3],
            [(1, 0, 0), (0.8, 0.2, 0), (1, 0, 0)],
            strict=True,
        ):
            db.add(GridCommunity(grid_id=grid.id, community_id=c.id, category=category))
            v = VisualEmbedding(embedder.model, 3, vector)
            db.add(
                CommunityReferencePhoto(
                    community_id=c.id,
                    vk_post_id=i,
                    vk_photo_owner_id=-i,
                    vk_photo_id=i,
                    posted_at=utcnow() - timedelta(days=2),
                    width=1000,
                    height=1000,
                    embedding=serialize_embedding(v),
                    embedding_model=v.model,
                    embedding_dimensions=3,
                )
            )
        target, same, other = [c.id for c in communities]
        gid = grid.id
    archive = SimpleNamespace(photo=lambda row, category: candidate(row.vk_photo_id))
    pool, _ = await VKCategoryArchivePhotoProvider(archive, visual).shortlist(
        target, "24 регион", None, gid
    )
    assert pool and {i.reference.community_id for i in pool} == {same}
    assert all(i.region_status == "same_region" for i in pool)
    assert other not in {i.reference.community_id for i in pool}


@pytest.mark.integration
async def test_hard_source_tiers_old_own_archive_cooldown_and_phash(sessions, tmp_path):
    from dropgrid.db.models import CommunityMediaUsage

    visual = VisualLibrary(sessions, LocalMediaStorage(tmp_path), None)
    now = utcnow()
    async with sessions() as db, db.begin():
        c = Community(domain="target")
        db.add(c)
        await db.flush()
        cid = c.id
        db.add(
            CommunityContentProfile(
                community_id=cid,
                archive_reuse_enabled=True,
                archive_reuse_min_age_days=180,
                archive_reuse_max_age_days=540,
            )
        )
        db.add(
            CommunityMediaUsage(
                community_id=cid,
                source_provider="fake",
                source_identity="used",
                sha256="a" * 64,
                perceptual_hash="0000000000000000",
                first_used_at=now - timedelta(days=5),
                last_used_at=now - timedelta(days=5),
                use_count=1,
            )
        )

    def ref(age, community=cid, id=1):
        return CommunityReferencePhoto(
            id=uuid4(),
            community_id=community,
            vk_photo_owner_id=-id,
            vk_photo_id=id,
            posted_at=now - timedelta(days=age),
        )

    old = PoolCandidate(candidate(1), "vk_archive", reference=ref(400))
    fresh = PoolCandidate(candidate(2), "vk_archive", reference=ref(200, id=2))
    same = PoolCandidate(
        candidate(3),
        "vk_category_archive",
        reference=ref(10, uuid4(), 3),
        region_status="same_region",
        region_key="krasnoyarsk_krai",
    )
    generic = PoolCandidate(candidate(4), "pinterest")
    mismatch = PoolCandidate(
        candidate(5),
        "vk_category_archive",
        region_status="same_region",
        region_key="novosibirsk_oblast",
    )
    cooldown = PoolCandidate(candidate(6), "vk_archive", reference=ref(400, id=6), sha256="a" * 64)
    near = PoolCandidate(
        candidate(7), "vk_archive", reference=ref(400, id=7), perceptual_hash="0000000000000001"
    )
    ranked = await rank_pool(
        visual,
        cid,
        [old, fresh, same, generic, mismatch, cooldown, near],
        PhotoQueryBuilder().build("24 регион"),
        now=now,
    )
    assert ranked == [same, old, generic]
    assert old.region_status == "own_community"
    # The global 180-day policy is unchanged outside regional contexts.
    assert fresh in await rank_pool(
        visual, cid, [fresh], PhotoQueryBuilder().build("авто"), now=now
    )


@pytest.mark.integration
async def test_cached_vk_import_cannot_hide_other_region_or_own_archive_policy(sessions, tmp_path):
    from dropgrid.db.models import MediaAsset, MediaProviderImport

    visual = VisualLibrary(sessions, LocalMediaStorage(tmp_path), None)
    async with sessions() as db, db.begin():
        grid = Grid(name="Provenance fixture")
        target = Community(domain="target")
        other = Community(domain="other")
        db.add_all([grid, target, other])
        await db.flush()
        db.add_all(
            [
                GridCommunity(grid_id=grid.id, community_id=target.id, category="24 регион"),
                GridCommunity(grid_id=grid.id, community_id=other.id, category="54 регион"),
            ]
        )
        assets = []
        for i, source in enumerate([target, other], 1):
            asset = MediaAsset(
                storage_key=f"{i}.jpg",
                category="24 регион",
                provider="vk_archive",
                provider_asset_id=str(i),
            )
            db.add(asset)
            await db.flush()
            assets.append(asset)
            db.add(
                MediaProviderImport(
                    provider="vk_archive",
                    provider_asset_id=str(i),
                    media_asset_id=asset.id,
                    source_url="https://vk.com/photo-1_1",
                    creator_name="",
                    license_code="configured",
                    source_community_id=source.id,
                    source_posted_at=utcnow() - timedelta(days=200),
                )
            )
        cid, gid = target.id, grid.id
    pool = [PoolCandidate(candidate(i + 1), "library", asset=a) for i, a in enumerate(assets)]
    assert (
        await rank_pool(visual, cid, pool, PhotoQueryBuilder().build("24 регион"), grid_id=gid)
        == []
    )
    assert pool[0].region_status == "own_import_requires_archive_policy"
    assert pool[1].region_status == "mismatch"
