from datetime import timedelta
from types import SimpleNamespace

import pytest

from dropgrid.db.models import Community, CommunityReferencePhoto, utcnow
from dropgrid.photos.category_archive import VKCategoryArchivePhotoProvider, context_matches
from dropgrid.photos.images import LocalMediaStorage
from dropgrid.photos.visual import FakeVisualEmbedder, VisualEmbedding, serialize_embedding
from dropgrid.photos.visual_library import VisualLibrary


def test_model_context_outranks_broad_category():
    assert context_matches("БМВ", "БМВ Е60", "Target", "БМВ", "BMW E60", "Source")
    assert not context_matches("БМВ", "БМВ Е60", "Target", "БМВ", "BMW E38", "Source")
    assert not context_matches("БМВ", "БМВ Е60", "Target", "БМВ", None, "BMW general")
    assert not context_matches("ХОНДА АККОРД", None, "Accord", "ХОНДА АККОРД", None, "Honda Civic")
    assert context_matches("Кошки", None, "Cats A", "кошки", None, "Cats B")
    assert not context_matches("Кошки", None, "Cats", "собаки", None, "Dogs")


@pytest.mark.integration
async def test_shortlist_excludes_target_wrong_models_and_uses_embeddings(sessions, tmp_path):
    embedder = FakeVisualEmbedder()
    visual = VisualLibrary(sessions, LocalMediaStorage(tmp_path), embedder)
    async with sessions() as s, s.begin():
        target = Community(domain="target", category="БМВ", name="BMW E60")
        good = Community(domain="source", category="БМВ", name="BMW E60")
        wrong = Community(domain="wrong", category="БМВ", name="BMW E38")
        s.add_all([target, good, wrong])
        await s.flush()
        for community, i, vector in (
            (target, 1, (1, 0, 0)),
            (good, 2, (1, 0, 0)),
            (good, 3, (0, 1, 0)),
            (wrong, 4, (1, 0, 0)),
        ):
            v = VisualEmbedding(embedder.model, 3, vector)
            s.add(
                CommunityReferencePhoto(
                    community_id=community.id,
                    vk_post_id=i,
                    vk_photo_owner_id=-i,
                    vk_photo_id=i,
                    posted_at=utcnow() - timedelta(days=1),
                    width=1000,
                    height=1000,
                    sha256=f"{i:064x}",
                    embedding=serialize_embedding(v),
                    embedding_model=v.model,
                    embedding_dimensions=3,
                )
            )
        cid, good_id = target.id, good.id
    archive = SimpleNamespace(
        photo=lambda row, category: __import__("photo_fixtures").candidate(str(row.vk_photo_id))
    )
    # No network or materialization during embedding-first shortlist.
    provider = VKCategoryArchivePhotoProvider(archive, visual)
    pool, stats = await provider.shortlist(cid, "БМВ", "BMW E60")
    assert [item.reference.vk_photo_id for item in pool] == [2, 3]
    assert all(
        item.reference.community_id == good_id and not item.photo.publication_eligible
        for item in pool
    )
    assert stats.candidate_rows == stats.compatible_embeddings == 2 and stats.shortlist == 2
    assert stats.source_communities == [good_id] and embedder.calls == 0


def test_canonical_vk_photo_dedup_across_source_names():
    from photo_fixtures import candidate

    from dropgrid.photos.pool import PoolCandidate, deduplicate_pool

    photo = SimpleNamespace(vk_photo_owner_id=-123, vk_photo_id=456)
    own = PoolCandidate(
        candidate("own").model_copy(update={"provider": "vk_archive"}),
        "vk_archive",
        reference=photo,
    )
    other = PoolCandidate(
        candidate("other").model_copy(update={"provider": "vk_category_archive"}),
        "vk_category_archive",
        reference=photo,
    )
    assert deduplicate_pool([own, other]) == [own]


@pytest.mark.integration
async def test_category_preview_degrades_safely_on_read_failure(sessions, tmp_path, monkeypatch):
    from photo_fixtures import candidate

    from dropgrid.photos.category_archive import CategoryLibraryStats
    from dropgrid.photos.domain import PhotoError
    from dropgrid.photos.pool import PoolCandidate

    visual = VisualLibrary(sessions, LocalMediaStorage(tmp_path), FakeVisualEmbedder())

    async def prepare(row):
        raise PhotoError("vk_credentials_unavailable")

    provider = VKCategoryArchivePhotoProvider(SimpleNamespace(prepare=prepare), visual)

    async def shortlist(*args):
        return [
            PoolCandidate(candidate("x"), "vk_category_archive", reference=SimpleNamespace())
        ], CategoryLibraryStats(candidate_rows=1, shortlist=1)

    monkeypatch.setattr(provider, "shortlist", shortlist)
    # Empty rank pool still requires a real target for profile/usage reads.
    async with sessions() as session, session.begin():
        target = Community(domain="target")
        session.add(target)
        await session.flush()
        identity = target.id
    pool, stats, warnings = await provider.preview(identity, "cats", None)
    assert (
        pool == [] and stats.shortlist == 1 and warnings == ["category_library_photo_unavailable"]
    )
