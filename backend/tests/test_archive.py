from datetime import UTC, datetime, timedelta

import pytest
from photo_fixtures import candidate

from dropgrid.db.models import CommunityMediaUsage, CommunityReferencePhoto
from dropgrid.photos.domain import PhotoSearch
from dropgrid.photos.pool import PoolCandidate, deduplicate_pool, exclude_self_reference
from dropgrid.photos.reference_schemas import ProfileInput
from dropgrid.photos.references import eligible_for_archive_reuse
from dropgrid.photos.rotation import archive_age_penalty, recently_used
from dropgrid.photos.visual import CommunityVisualRanker, VisualEmbedding, VisualScore, rank_photo

NOW = datetime(2026, 10, 9, tzinfo=UTC)


@pytest.mark.parametrize("age,expected", [(179, False), (180, True), (540, True), (541, False)])
def test_archive_age_window(age, expected):
    assert (
        eligible_for_archive_reuse(NOW - timedelta(days=age), NOW, 180, True, max_age_days=540)
        is expected
    )
    assert not eligible_for_archive_reuse(
        NOW - timedelta(days=age), NOW, 180, False, max_age_days=540
    )


@pytest.mark.parametrize("minimum,maximum", [(180, 180), (540, 180), (0, 3651)])
def test_invalid_profile_window(minimum, maximum):
    with pytest.raises(ValueError):
        ProfileInput(archive_reuse_min_age_days=minimum, archive_reuse_max_age_days=maximum)


def test_age_penalty_small_bounded_and_no_infinite_reward():
    assert archive_age_penalty(NOW - timedelta(days=180), NOW, 180) == pytest.approx(0.03)
    assert 0 < archive_age_penalty(NOW - timedelta(days=270), NOW, 180) < 0.03
    assert archive_age_penalty(NOW - timedelta(days=365), NOW, 180) == 0
    assert archive_age_penalty(NOW - timedelta(days=1000), NOW, 180) == 0


@pytest.mark.parametrize("reason", ["identity", "sha", "near"])
def test_self_similarity_exclusion_and_zero_remaining(reason):
    row = CommunityReferencePhoto(vk_photo_owner_id=-123, vk_photo_id=1)
    ref = CommunityReferencePhoto(
        vk_photo_owner_id=-123, vk_photo_id=2, sha256="b" * 64, perceptual_hash="0000000000000000"
    )
    pool = PoolCandidate(
        candidate(),
        "vk_archive",
        reference=row,
        sha256="a" * 64,
        perceptual_hash="ffffffffffffffff",
    )
    if reason == "identity":
        ref.vk_photo_id = 1
    elif reason == "sha":
        ref.sha256 = pool.sha256
    else:
        pool.perceptual_hash = "0000000000000001"
    assert exclude_self_reference(pool, ref)
    vector = VisualEmbedding("fake", 2, (1, 0))
    refs = [] if exclude_self_reference(pool, ref) else [vector]
    assert CommunityVisualRanker().score(vector, refs).top_k_mean is None


def test_no_duplicate_exclusion_for_unrelated_photo():
    item = PoolCandidate(
        candidate(),
        "vk_archive",
        reference=CommunityReferencePhoto(vk_photo_owner_id=-123, vk_photo_id=1),
        sha256="a" * 64,
        perceptual_hash="ffffffffffffffff",
    )
    ref = CommunityReferencePhoto(
        vk_photo_owner_id=-123, vk_photo_id=2, sha256="b" * 64, perceptual_hash="0000000000000000"
    )
    assert not exclude_self_reference(item, ref)


def test_source_order_is_not_rank_priority():
    search = PhotoSearch(query="truck")
    pixabay = candidate(provider="pixabay")
    archive = candidate(provider="vk_archive")
    strong = VisualScore(0.9, 0.9, 10)
    weak = VisualScore(0.2, 0.2, 10)
    assert (
        rank_photo(pixabay, search, strong).final_score
        > rank_photo(archive, search, weak).final_score
    )
    assert (
        rank_photo(archive, search, strong).final_score
        > rank_photo(pixabay, search, weak).final_score
    )


@pytest.mark.parametrize("reason", ["identity", "sha", "near"])
def test_cooldown_different_clock_and_identity_before_import(reason):
    row = CommunityMediaUsage(
        source_provider="vk_archive",
        source_identity="-123_1",
        sha256="a" * 64,
        perceptual_hash="0000000000000000",
        last_used_at=NOW - timedelta(days=179),
    )
    provider, identity, sha, phash = "pixabay", "2", "b" * 64, "ffffffffffffffff"
    if reason == "identity":
        provider, identity = "vk_archive", "-123_1"
    elif reason == "sha":
        sha = "a" * 64
    else:
        phash = "0000000000000001"
    assert recently_used([row], provider, identity, sha, phash, NOW)
    row.last_used_at = NOW - timedelta(days=180)
    assert not recently_used([row], provider, identity, sha, phash, NOW)
    assert not recently_used([], provider, identity, sha, phash, NOW)


def test_cross_source_pool_dedup_deterministic():
    a = PoolCandidate(candidate(provider="pixabay"), "pixabay", sha256="a" * 64)
    b = PoolCandidate(candidate(provider="vk_archive"), "vk_archive", sha256="a" * 64)
    assert len(deduplicate_pool([a, b])) == 1
    assert deduplicate_pool([a, b])[0].identity == deduplicate_pool([b, a])[0].identity
