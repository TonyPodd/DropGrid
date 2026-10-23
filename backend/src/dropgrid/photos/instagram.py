"""Instagram profiles an operator listed: synced photos and per-community choice.

Sync stores each new photo of a profile once as a MediaAsset (provider "instagram")
with its CLIP embedding, and keeps a profile centroid. For a community the bot picks
the profiles whose photos look most like the community's wall, then only the photos
that fit it about as well as the community's own photos fit each other. Ranking and
selection then treat them like any other candidate.
"""

import asyncio
import re
import statistics
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.config import Settings
from dropgrid.db.models import InstagramPhoto, InstagramProfile, MediaAsset, utcnow
from dropgrid.integrations.instagram import (
    InstagramError,
    InstagramImage,
    InstagramPost,
)
from dropgrid.photos.domain import PhotoError, PhotoPolicy
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.history import ProposedPhotos
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.photos.pool import PoolCandidate
from dropgrid.photos.visual import (
    CommunityVisualRanker,
    VisualEmbedder,
    VisualEmbedding,
    cosine_similarity,
    deserialize_embedding,
    serialize_embedding,
)
from dropgrid.photos.visual_library import VisualLibrary

PROVIDER = "instagram"
# Profiles considered for one community, and photos it gets from them at most.
PROFILES_PER_COMMUNITY = 3
PHOTOS_PER_COMMUNITY = 24
# A photo fits when it is at least this close to the community's photos, relative to
# how close the community's own photos are to each other.
FIT_SHARE = 0.9
SYNC_LEASE = timedelta(minutes=15)
# Daily reads take a page of the newest posts and stop at known ones; the scraper is
# paid per post.
REFRESH_POSTS = 50
# One tick stores photos for at most this long, so campaign preparation never waits
# behind a large history; the reading continues on the next tick.
TICK_SECONDS = 120
_clock = time.monotonic
# Errors read again sooner than the daily cycle: slowed down / asked to log in.
RETRY_HOURS = {"instagram_rate_limited": 1, "instagram_login_required": 6}


class InstagramClient(Protocol):
    @property
    def configured(self) -> bool: ...

    async def posts_page(
        self, username: str, cursor: str | None, limit: int
    ) -> tuple[list[InstagramPost], str | None]: ...


@dataclass(frozen=True)
class InstagramPolicy(PhotoPolicy):
    trusted_hosts: frozenset[str] = frozenset({"cdninstagram.com", "fbcdn.net"})

    def trusted_host(self, host: str) -> bool:
        return any(host == root or host.endswith("." + root) for root in self.trusted_hosts)


def caption_tags(caption: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\w+", caption.casefold())))[:50]


def centroid(vectors: list[VisualEmbedding]) -> VisualEmbedding | None:
    if not vectors:
        return None
    first = vectors[0]
    sums = [sum(v.vector[i] for v in vectors) for i in range(first.dimensions)]
    try:
        return VisualEmbedding(first.model, first.dimensions, tuple(sums))
    except PhotoError:
        return None


class InstagramSync:
    """Reads one due profile per tick; read-only towards Instagram."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: InstagramClient,
        downloader: PhotoDownloader,
        storage: LocalMediaStorage,
        embedder: VisualEmbedder | None,
        settings: Settings,
    ) -> None:
        self.sessions, self.client, self.downloader = sessions, client, downloader
        self.storage, self.embedder, self.settings = storage, embedder, settings

    async def tick(self) -> int:
        if not self.client.configured or self.embedder is None:
            return 0
        now = utcnow()
        async with self.sessions() as session, session.begin():
            profile = await session.scalar(
                select(InstagramProfile)
                .where(
                    InstagramProfile.enabled.is_(True),
                    or_(
                        InstagramProfile.last_synced_at.is_(None),
                        InstagramProfile.last_synced_at
                        < now - timedelta(hours=self.settings.instagram_sync_hours),
                        # An unfinished (or interrupted) reading continues right away.
                        InstagramProfile.status == "syncing",
                    ),
                    or_(
                        InstagramProfile.sync_lease_until.is_(None),
                        InstagramProfile.sync_lease_until < now,
                    ),
                )
                .order_by(
                    InstagramProfile.last_synced_at.asc().nulls_first(),
                    InstagramProfile.created_at,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if profile is None:
                return 0
            profile.status, profile.sync_lease_until = "syncing", now + SYNC_LEASE
            profile_id, username = profile.id, profile.username
            full = profile.full_read_at is None
            cursor = profile.history_cursor if full else None
            stored = profile.photo_count
        cap = self.settings.instagram_max_photos_per_profile
        deadline = _clock() + TICK_SECONDS
        error, complete = None, False
        while True:
            page_start = cursor
            try:
                posts, cursor = await self.client.posts_page(
                    username, page_start, max(1, cap - stored) if full else REFRESH_POSTS
                )
            except InstagramError as exc:
                error = exc.code
                break
            new, stopped = 0, False
            for post in posts:
                for image in post.images:
                    if _clock() > deadline or stored >= cap:
                        stopped = True
                        break
                    try:
                        if await self._store(profile_id, username, post, image):
                            new += 1
                            stored += 1
                    except (PhotoError, OSError):
                        continue  # one unusable photo never blocks the rest
                if stopped:
                    break
            if stopped:
                # Out of time: continue from this page next tick (known photos are
                # skipped). At the photo cap the history is complete.
                complete = stored >= cap
                cursor = None if complete else page_start
                break
            if cursor is None or (not full and not new):
                # End of history, or a refresh reached posts that are known already.
                complete = True
                break
        await self._finish(profile_id, error, complete, cursor)
        return 1

    async def _store(
        self, profile_id: UUID, username: str, post: InstagramPost, image: InstagramImage
    ) -> bool:
        """Store one photo; False when it is known already."""
        assert self.embedder is not None
        async with self.sessions() as session:
            if await session.scalar(
                select(InstagramPhoto.id).where(InstagramPhoto.media_id == image.media_id)
            ):
                return False
        data = await self.downloader.download(image.url)
        normalized = await asyncio.to_thread(normalize_image, data, InstagramPolicy())
        vector = await self.embedder.embed_image(normalized.data)
        async with self.sessions() as session, session.begin():
            asset = await session.scalar(
                select(MediaAsset).where(
                    or_(
                        MediaAsset.sha256 == normalized.sha256,
                        (MediaAsset.provider == PROVIDER)
                        & (MediaAsset.provider_asset_id == image.media_id),
                    )
                )
            )
            if asset is None:
                key = await asyncio.to_thread(self.storage.write, normalized)
                asset = MediaAsset(
                    storage_key=key,
                    source_url=post.permalink,
                    tags=caption_tags(post.caption),
                    provider=PROVIDER,
                    provider_asset_id=image.media_id,
                    creator_name=username,
                    license_code="instagram-profile",
                    license_name="Publication rights unverified",
                    license_url=post.permalink,
                    width=normalized.width,
                    height=normalized.height,
                    mime_type="image/jpeg",
                    byte_size=len(normalized.data),
                    sha256=normalized.sha256,
                    perceptual_hash=normalized.perceptual_hash,
                    visual_embedding=serialize_embedding(vector),
                    visual_embedding_model=vector.model,
                    visual_embedding_dimensions=vector.dimensions,
                )
                session.add(asset)
                await session.flush()
            await session.execute(
                insert(InstagramPhoto)
                .values(
                    profile_id=profile_id,
                    media_id=image.media_id,
                    permalink=post.permalink,
                    posted_at=post.posted_at,
                    media_asset_id=asset.id,
                    created_at=utcnow(),
                )
                .on_conflict_do_nothing(index_elements=["media_id"])
            )
        return True

    async def _finish(
        self, profile_id: UUID, error: str | None, complete: bool, cursor: str | None
    ) -> None:
        assert self.embedder is not None
        async with self.sessions() as session, session.begin():
            profile = await session.get(InstagramProfile, profile_id, with_for_update=True)
            if profile is None:
                return  # removed while syncing
            profile.sync_lease_until = None
            if error or complete:
                profile.last_synced_at = utcnow()
                profile.history_cursor = None
                if complete and not error:
                    profile.full_read_at = profile.full_read_at or profile.last_synced_at
            else:
                profile.history_cursor = cursor
            retry = RETRY_HOURS.get(error or "")
            if retry is not None and profile.last_synced_at:
                # Try again sooner than tomorrow, without hammering a refusing Instagram.
                profile.last_synced_at -= timedelta(
                    hours=max(0, self.settings.instagram_sync_hours - retry)
                )
            rows = (
                await session.execute(
                    select(MediaAsset.visual_embedding)
                    .join(InstagramPhoto, InstagramPhoto.media_asset_id == MediaAsset.id)
                    .where(
                        InstagramPhoto.profile_id == profile_id,
                        MediaAsset.enabled.is_(True),
                        MediaAsset.visual_embedding_model == self.embedder.model,
                        MediaAsset.visual_embedding_dimensions == self.embedder.dimensions,
                    )
                )
            ).all()
            vectors = []
            for (blob,) in rows:
                try:
                    vectors.append(
                        deserialize_embedding(blob, self.embedder.model, self.embedder.dimensions)
                    )
                except PhotoError:
                    continue
            center = centroid(vectors)
            profile.photo_count = len(vectors)
            profile.centroid = serialize_embedding(center) if center else None
            profile.centroid_model = center.model if center else None
            profile.centroid_dimensions = center.dimensions if center else None
            if error:
                profile.status, profile.error_code = "error", error
            elif not complete:
                profile.status, profile.error_code = "syncing", None  # continues next tick
            elif not vectors:
                profile.status, profile.error_code = "error", "instagram_no_photos"
            else:
                profile.status, profile.error_code = "ready", None


class InstagramPhotoProvider:
    """Photos of the listed profiles that fit one community."""

    name = PROVIDER

    def __init__(self, visual: VisualLibrary) -> None:
        self.visual, self.sessions = visual, visual.sessions

    async def candidates(
        self, community_id: UUID, exclude: ProposedPhotos | None = None
    ) -> list[PoolCandidate]:
        """`exclude`: photos used by other campaigns lately, left out before the shortlist."""
        embedder = self.visual.embedder
        if embedder is None:
            return []
        refs, _, _ = await self.visual.references(community_id)
        center = centroid(refs)
        if len(refs) < 2 or center is None:
            return []  # without the community's own photos fit cannot be judged
        async with self.sessions() as session:
            profiles = (
                await session.scalars(
                    select(InstagramProfile).where(
                        InstagramProfile.enabled.is_(True),
                        InstagramProfile.status == "ready",
                        InstagramProfile.centroid.is_not(None),
                        InstagramProfile.centroid_model == embedder.model,
                        InstagramProfile.centroid_dimensions == embedder.dimensions,
                    )
                )
            ).all()
            closeness = []
            for profile in profiles:
                assert profile.centroid
                try:
                    vector = deserialize_embedding(
                        profile.centroid, embedder.model, embedder.dimensions
                    )
                except PhotoError:
                    continue
                closeness.append((cosine_similarity(center, vector), str(profile.id), profile.id))
            chosen = [pid for *_, pid in sorted(closeness, reverse=True)[:PROFILES_PER_COMMUNITY]]
            if not chosen:
                return []
            assets = (
                await session.scalars(
                    select(MediaAsset)
                    .join(InstagramPhoto, InstagramPhoto.media_asset_id == MediaAsset.id)
                    .where(
                        InstagramPhoto.profile_id.in_(chosen),
                        MediaAsset.enabled.is_(True),
                        MediaAsset.visual_embedding_model == embedder.model,
                        MediaAsset.visual_embedding_dimensions == embedder.dimensions,
                    )
                )
            ).all()
        ranker = CommunityVisualRanker()
        own = [
            ranker.score(ref, refs[:i] + refs[i + 1 :]).top_k_mean or 0.0
            for i, ref in enumerate(refs)
        ]
        threshold = FIT_SHARE * statistics.median(own)
        fitting = []
        for asset in assets:
            assert asset.visual_embedding
            try:
                vector = deserialize_embedding(
                    asset.visual_embedding, embedder.model, embedder.dimensions
                )
            except PhotoError:
                continue
            score = ranker.score(vector, refs).top_k_mean or 0.0
            if score >= threshold:
                fitting.append((score, asset, vector))
        fitting.sort(key=lambda item: (-item[0], str(item[1].id)))
        from dropgrid.photos.planner import CampaignMediaPlanner

        result = []
        for _, asset, vector in fitting:
            item = PoolCandidate(
                CampaignMediaPlanner._asset_candidate(asset),
                PROVIDER,
                asset=asset,
                embedding=vector,
                sha256=asset.sha256,
                perceptual_hash=asset.perceptual_hash,
            )
            if exclude is not None and item in exclude:
                continue
            result.append(item)
            if len(result) >= PHOTOS_PER_COMMUNITY:
                break
        return result
