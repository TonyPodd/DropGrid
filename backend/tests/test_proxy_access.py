from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from test_photo_integration import prepared

from dropgrid.api.app import create_app
from dropgrid.config import Settings
from dropgrid.db.models import (
    MediaAsset,
    PhotoReviewer,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    Submission,
)
from dropgrid.photos.validation import create_batch

KEY = "test-internal-proxy-key-32-characters-minimum"


@asynccontextmanager
async def proxy_client(database_url):
    app = create_app(
        Settings(
            _env_file=None,
            app_env="production",
            database_url=database_url,
            trusted_proxy_enabled=True,
            trusted_proxy_key=KEY,
            pinterest_direct_enabled=False,
        )
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http


@pytest.mark.integration
@pytest.mark.parametrize("actor", ["tony", "tima"])
async def test_both_authenticated_users_have_full_access(sessions, database_url, actor):
    async with proxy_client(database_url) as http:
        headers = {"x-dropgrid-remote-user": actor, "x-dropgrid-proxy-key": KEY}
        for path in [
            "/api/v1/accounts",
            "/api/v1/campaigns",
            "/api/v1/grids",
            "/api/v1/photo-ranking",
            "/docs",
        ]:
            assert (await http.get(path, headers=headers)).status_code == 200
        assert (
            await http.get("/api/v1/accounts", headers={"x-dropgrid-remote-user": actor})
        ).status_code == 401
        assert (
            await http.get("/api/v1/accounts", headers={**headers, "x-dropgrid-proxy-key": "spoof"})
        ).status_code == 401
        assert (
            await http.get(
                "/api/v1/accounts", headers={**headers, "x-dropgrid-remote-user": "unknown"}
            )
        ).status_code == 401


@pytest.mark.integration
async def test_proxy_reviewer_mapping_cannot_be_overridden(sessions, database_url):
    cid = await prepared(sessions, count=1)
    async with sessions() as db, db.begin():
        tima = PhotoReviewer(display_name="Tima")
        tony = PhotoReviewer(display_name="Tony")
        db.add_all([tima, tony])
        await db.flush()
        sub = await db.scalar(select(Submission).where(Submission.campaign_id == cid))
        asset = MediaAsset(storage_key="fixture.jpg")
        db.add(asset)
        await db.flush()
        sub.media_asset_id = asset.id
        selection = PhotoSelectionSession(
            campaign_id=cid,
            submission_id=sub.id,
            community_id=sub.community_id,
            category="Cars",
            proposed_rank=1,
            baseline_rank=1,
        )
        db.add(selection)
        await db.flush()
        db.add(
            PhotoSelectionCandidate(
                selection_session_id=selection.id,
                rank=1,
                provider="library",
                source_identity="fixture",
                media_asset_id=sub.media_asset_id,
                features={},
                candidate={},
            )
        )
        await db.flush()
        batch = await create_batch(db, cid, "Test", 1, tima.id, tony.id)
    async with proxy_client(database_url) as http:
        for actor, reviewer in [("tima", tima), ("tony", tony)]:
            headers = {"x-dropgrid-remote-user": actor, "x-dropgrid-proxy-key": KEY}
            response = await http.get(f"/api/v1/review-batches/{batch.id}", headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["reviewer_id"] == str(reviewer.id)
            assert data["trusted_reviewer"]["display_name"] == reviewer.display_name
            assert data["owner"] is True
        headers = {"x-dropgrid-remote-user": "tima", "x-dropgrid-proxy-key": KEY}
        assert (
            await http.put(
                f"/api/v1/review-batches/{batch.id}/cursor",
                headers=headers,
                json={"position": 0, "reviewer_id": str(tony.id)},
            )
        ).status_code == 403
        assert (
            await http.put(
                f"/api/v1/review-batches/{batch.id}/cursor",
                headers=headers,
                json={"position": 0, "reviewer_id": str(tima.id), "mode": "all"},
            )
        ).status_code == 200
        assert (await http.get(f"/api/v1/review-batches/{batch.id}", headers=headers)).json()[
            "current_filter"
        ] == "all"
    async with sessions() as db:
        assert len((await db.scalars(select(Submission))).all()) == 1


def test_production_token_import_requires_trusted_authenticated_identity():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from dropgrid.api.access import owner_token_access

    for actor in ["tony", "tima"]:
        owner_token_access(
            SimpleNamespace(state=SimpleNamespace(remote_user=actor)),
            Settings(_env_file=None, app_env="production"),
        )
    with pytest.raises(HTTPException):
        owner_token_access(
            SimpleNamespace(state=SimpleNamespace()), Settings(_env_file=None, app_env="production")
        )
    owner_token_access(SimpleNamespace(state=SimpleNamespace()), Settings(_env_file=None))


@pytest.mark.integration
async def test_validation_refresh_preserves_assignments_and_old_batch(sessions, monkeypatch):
    from types import SimpleNamespace

    from dropgrid.db.models import PhotoPreviewCache
    from dropgrid.photos.domain import PhotoCandidate, PhotoQueryBuilder
    from dropgrid.photos.pool import PoolCandidate
    from dropgrid.photos.validation_refresh import refresh_snapshot
    from dropgrid.photos.visual import RankedPhoto

    cid = await prepared(sessions, count=1)
    async with sessions() as db, db.begin():
        sub = await db.scalar(select(Submission).where(Submission.campaign_id == cid))
        asset = MediaAsset(
            storage_key="original",
            provider="pixabay",
            provider_asset_id="original",
            width=1200,
            height=1200,
        )
        db.add(asset)
        await db.flush()
        sub.media_asset_id = asset.id
        sub.photo_source = "library"
        sub.photo_attention = ["original_warning"]
        selection = PhotoSelectionSession(
            campaign_id=cid,
            submission_id=sub.id,
            community_id=sub.community_id,
            category="Cars",
            proposed_rank=1,
            baseline_rank=1,
        )
        db.add(selection)
        await db.flush()
        photo = PhotoCandidate(
            provider="pixabay",
            provider_asset_id="original",
            source_page_url="https://pixabay.com/photos/original/",
            candidate_download_url="",
            width=1200,
            height=1200,
            creator_name="",
            license_code="pixabay-content-license",
            license_name="Pixabay",
            license_url="https://pixabay.com/service/license-summary/",
        )
        db.add(
            PhotoSelectionCandidate(
                selection_session_id=selection.id,
                rank=1,
                provider="library",
                source_identity="original",
                media_asset_id=asset.id,
                features={"final_score": 0.5},
                candidate=photo.model_dump(mode="json"),
            )
        )
        preview = PhotoPreviewCache(
            provider="pinterest",
            provider_asset_id="fixture",
            candidate={},
            storage_key="fixture",
            sha256="0" * 64,
            perceptual_hash="0" * 16,
        )
        db.add(preview)
        await db.flush()
        old_batch = await create_batch(db, cid, "old", 1, None)
    pin = photo.model_copy(
        update={
            "provider": "pinterest",
            "provider_asset_id": "fixture",
            "publication_eligible": False,
        }
    )

    async def fake_preview(*args, capture, **kwargs):
        capture.append(
            PoolCandidate(
                pin, "pinterest", preview_id=preview.id, score=RankedPhoto(0.4, None, 0.4, None, 0)
            )
        )
        return SimpleNamespace(category="Cars", desired_content=None, warnings=[])

    monkeypatch.setattr("dropgrid.photos.validation_refresh._photo_preview", fake_preview)
    planner = SimpleNamespace(
        sessions=sessions,
        settings=Settings(_env_file=None),
        policy=SimpleNamespace(plan_timeout_seconds=30),
        builder=PhotoQueryBuilder(),
    )
    fresh_id = await refresh_snapshot(planner, SimpleNamespace(), sub.id)
    async with sessions() as db, db.begin():
        fresh_sub = await db.get(Submission, sub.id)
        assert fresh_sub.media_asset_id == asset.id and fresh_sub.photo_source == "library"
        assert fresh_sub.photo_attention == ["original_warning"]
        options = (
            await db.scalars(
                select(PhotoSelectionCandidate).where(
                    PhotoSelectionCandidate.selection_session_id == fresh_id
                )
            )
        ).all()
        assert {c.provider for c in options} == {"library", "pinterest"}
        assert all(not c.displayed and not c.selected for c in options)
        assert (await db.get(PhotoSelectionSession, selection.id)).confirmed_at is None
        new_batch = await create_batch(db, cid, "fresh", 1, None, selection_ids={fresh_id})
        from dropgrid.db.models import PhotoReviewBatchItem

        assert (
            await db.get(PhotoReviewBatchItem, (old_batch.id, 0))
        ).selection_session_id == selection.id
        assert (
            await db.get(PhotoReviewBatchItem, (new_batch.id, 0))
        ).selection_session_id == fresh_id
