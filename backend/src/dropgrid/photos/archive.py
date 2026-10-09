"""Age-based read-only discovery and lazy, same-community archive candidates."""

import asyncio
import hashlib
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects.postgresql import insert

from dropgrid.db.models import (
    Account,
    Community,
    CommunityContentProfile,
    CommunityReferencePhoto,
    utcnow,
)
from dropgrid.domain.enums import AccountStatus
from dropgrid.integrations.vk.errors import VKError
from dropgrid.integrations.vk.models import WallPosts
from dropgrid.photos.archive_retrieval import WallDateSeeker, stratified_sample
from dropgrid.photos.domain import PhotoCandidate, PhotoError, PhotoPolicy, PhotoQueryBuilder
from dropgrid.photos.images import normalize_image
from dropgrid.photos.reference_schemas import ArchiveSyncRead
from dropgrid.photos.references import (
    CommunityReferenceCollector,
    eligible_for_archive_reuse,
    representative_photo,
)
from dropgrid.photos.timings import stage
from dropgrid.photos.visual import deserialize_embedding, serialize_embedding
from dropgrid.services.catalog import ConflictError, get_entity

ARCHIVE_TIMEOUT_SECONDS = 300
ARCHIVE_MAX_PAGES = 50
ARCHIVE_CANDIDATE_LIMIT = 28
ARCHIVE_MAX_POSTS = 5000


def archive_identity(row: CommunityReferencePhoto) -> str:
    return f"{row.vk_photo_owner_id}_{row.vk_photo_id}"


class ArchiveDiscovery:
    def __init__(self, collector: CommunityReferenceCollector) -> None:
        self.collector = collector

    async def sync(
        self,
        community_id: UUID,
        account_id: UUID | None = None,
        max_pages: int = 20,
        *,
        now: datetime | None = None,
    ) -> ArchiveSyncRead:
        if type(max_pages) is not int or not 1 <= max_pages <= ARCHIVE_MAX_PAGES:
            raise ConflictError("Invalid archive scan limit")
        now = now or utcnow()
        service = self.collector
        lease = uuid4()
        report = ArchiveSyncRead()
        async with service.sessions() as s, s.begin():
            c = await get_entity(s, Community, community_id)
            if not c.vk_group_id or not c.is_active:
                raise ConflictError("Resolve an active Community first")
            group = c.vk_group_id
            await s.execute(
                insert(CommunityContentProfile)
                .values(community_id=community_id)
                .on_conflict_do_nothing()
            )
            profile = await s.get(CommunityContentProfile, community_id, with_for_update=True)
            assert profile
            if (
                profile.archive_lease_until
                and profile.archive_lease_until > utcnow()
                or profile.sync_lease_until
                and profile.sync_lease_until > utcnow()
            ):
                raise ConflictError("Archive discovery already running")
            profile.archive_lease_token, profile.archive_lease_until = (
                lease,
                utcnow() + timedelta(minutes=6),
            )
            min_age, max_age, enabled = (
                profile.archive_reuse_min_age_days,
                profile.archive_reuse_max_age_days,
                profile.archive_reuse_enabled,
            )
            query = (
                select(Account.id)
                .where(
                    Account.status == AccountStatus.active,
                    Account.vk_user_id.is_not(None),
                    Account.encrypted_access_token.is_not(None),
                )
                .order_by(Account.created_at, Account.id)
            )
            if account_id:
                query = query.where(Account.id == account_id)
            aid = await s.scalar(query.limit(1))
        finished = False
        try:
            if aid is None:
                report.warnings.append("vk_credentials_unavailable")
                return report
            token = await service.tokens.get_token(aid)
            lower = now - timedelta(days=max_age)
            upper = now - timedelta(days=min_age)
            async with asyncio.timeout(ARCHIVE_TIMEOUT_SECONDS):

                async def fetch(offset: int, count: int) -> WallPosts:
                    return await service.client.get_community_wall_history(
                        group, access_token=token, account_id=aid, count=count, offset=offset
                    )

                seeker = WallDateSeeker(fetch)
                start = await seeker.boundary(upper)
                end = await seeker.boundary(lower) if start is not None else None
                report.seek_calls = seeker.calls
                report.seek_posts_inspected = seeker.posts_inspected
                report.warnings.extend(seeker.warnings)
                if start is None or end is None:
                    report.warnings.append("archive_seek_bounded_fallback")
                offset = max(0, (start or 0) - 100)
                report.start_offset = offset
                stop_offset = end + 200 if end is not None else None
                for _ in range(max_pages):
                    page = await service.client.get_community_wall_history(
                        group,
                        access_token=token,
                        account_id=aid,
                        count=100,
                        offset=offset,
                    )
                    report.pages_read += 1
                    metadata = []
                    chronology = [
                        post["date"]
                        for post in page.items
                        if not post.get("is_pinned")
                        and post.get("post_type") == "post"
                        and type(post.get("date")) is int
                        and 0 < post["date"] < 253402300799
                    ]
                    chronological = all(
                        a >= b for a, b in zip(chronology, chronology[1:], strict=False)
                    )
                    if not chronological:
                        report.warnings.append("archive_scan_unstable_chronology")
                    for post in page.items[:100]:
                        report.posts_scanned += 1
                        date = post.get("date")
                        if type(date) is int and date > 0:
                            # VK wall chronology has a pinned-post exception.
                            if (
                                date < lower.timestamp()
                                and not post.get("is_pinned")
                                and post.get("post_type") == "post"
                            ):
                                report.crossed_max_age = chronological
                        photo = representative_photo(post, group, service.policy)
                        if photo and lower <= photo.posted_at <= upper:
                            metadata.append(photo)
                    async with service.sessions() as s, s.begin():
                        p = await s.get(CommunityContentProfile, community_id, with_for_update=True)
                        if (
                            not p
                            or p.archive_lease_token != lease
                            or not p.archive_lease_until
                            or p.archive_lease_until <= utcnow()
                        ):
                            raise ConflictError("Archive discovery lease expired")
                        for photo in metadata:
                            old = await s.scalar(
                                select(CommunityReferencePhoto).where(
                                    CommunityReferencePhoto.community_id == community_id,
                                    CommunityReferencePhoto.vk_photo_owner_id == photo.owner_id,
                                    CommunityReferencePhoto.vk_photo_id == photo.photo_id,
                                )
                            )
                            if old:
                                old.archive_discovered = True
                                if not old.width or not old.height:
                                    old.width, old.height = photo.width, photo.height
                                old.reuse_eligible = eligible_for_archive_reuse(
                                    old.posted_at, now, min_age, enabled, old.enabled, max_age
                                )
                                report.candidates_existing += 1
                            else:
                                s.add(
                                    CommunityReferencePhoto(
                                        community_id=community_id,
                                        vk_post_id=photo.post_id,
                                        vk_photo_owner_id=photo.owner_id,
                                        vk_photo_id=photo.photo_id,
                                        posted_at=photo.posted_at,
                                        source_url=photo.url,
                                        width=photo.width,
                                        height=photo.height,
                                        is_style_reference=False,
                                        archive_discovered=True,
                                        reuse_eligible=enabled,
                                    )
                                )
                                await s.flush()
                                report.candidates_discovered += 1
                    report.end_offset = offset + len(page.items)
                    if report.crossed_max_age:
                        finished = True
                        break
                    offset += len(page.items)
                    if not page.items or offset >= page.count:
                        report.exhausted = finished = True
                        break
                    if report.posts_scanned >= ARCHIVE_MAX_POSTS or (
                        stop_offset is not None and offset >= stop_offset
                    ):
                        report.scan_bound_reached = True
                        report.warnings.append("archive_scan_bound_reached")
                        break
                else:
                    report.scan_bound_reached = True
                    report.warnings.append("archive_scan_bound_reached")
        except VKError as e:
            report.warnings.append("vk_read_" + type(e).__name__)
        except TimeoutError:
            report.warnings.append("archive_sync_timeout")
        finally:
            async with service.sessions() as s, s.begin():
                p = await s.get(CommunityContentProfile, community_id, with_for_update=True)
                if p and p.archive_lease_token == lease:
                    if finished:
                        p.archive_last_synced_at = utcnow()
                    p.archive_lease_token = p.archive_lease_until = None
            report.warnings = sorted(set(report.warnings))
        return report


class VKArchivePhotoProvider:
    name = "vk_archive"

    def __init__(self, collector: CommunityReferenceCollector) -> None:
        self.collector = collector
        self.locks: WeakValueDictionary[UUID, asyncio.Lock] = WeakValueDictionary()
        self.download_count = self.embedding_count = 0

    async def candidates(
        self,
        community_id: UUID,
        limit: int = ARCHIVE_CANDIDATE_LIMIT,
        *,
        now: datetime | None = None,
    ) -> list[CommunityReferencePhoto]:
        now = now or utcnow()
        async with self.collector.sessions() as s:
            c = await get_entity(s, Community, community_id)
            p = await s.get(CommunityContentProfile, community_id)
            if not c.is_active or not p or not p.archive_reuse_enabled:
                return []
            limit = min(max(limit, 1), ARCHIVE_CANDIDATE_LIMIT)
            query = select(CommunityReferencePhoto).where(
                CommunityReferencePhoto.community_id == community_id,
                CommunityReferencePhoto.archive_discovered.is_(True),
                CommunityReferencePhoto.enabled.is_(True),
                CommunityReferencePhoto.posted_at
                >= now - timedelta(days=p.archive_reuse_max_age_days),
                CommunityReferencePhoto.posted_at
                <= now - timedelta(days=p.archive_reuse_min_age_days),
                CommunityReferencePhoto.vk_photo_owner_id != 0,
                CommunityReferencePhoto.vk_photo_id > 0,
                CommunityReferencePhoto.width >= PhotoPolicy().min_short_side,
                CommunityReferencePhoto.height >= PhotoPolicy().min_short_side,
                CommunityReferencePhoto.width
                <= CommunityReferencePhoto.height * PhotoPolicy().max_aspect_ratio,
                CommunityReferencePhoto.height
                <= CommunityReferencePhoto.width * PhotoPolicy().max_aspect_ratio,
            )
            rows: list[CommunityReferencePhoto] = []
            width = (p.archive_reuse_max_age_days - p.archive_reuse_min_age_days) / 4
            for band in range(4):
                young = now - timedelta(days=p.archive_reuse_min_age_days + band * width)
                old = now - timedelta(days=p.archive_reuse_min_age_days + (band + 1) * width)
                boundary = (
                    CommunityReferencePhoto.posted_at >= old
                    if band == 3
                    else CommunityReferencePhoto.posted_at > old
                )
                rows.extend(
                    (
                        await s.scalars(
                            query.where(CommunityReferencePhoto.posted_at <= young, boundary)
                            .order_by(
                                func.md5(
                                    func.concat(
                                        str(community_id),
                                        ":",
                                        cast(CommunityReferencePhoto.vk_photo_owner_id, String),
                                        "_",
                                        cast(CommunityReferencePhoto.vk_photo_id, String),
                                        ":",
                                        str(now.date()),
                                    )
                                ),
                                CommunityReferencePhoto.id,
                            )
                            .limit(limit)
                        )
                    ).all()
                )
            return stratified_sample(
                rows,
                community_id,
                p.archive_reuse_min_age_days,
                p.archive_reuse_max_age_days,
                now,
                limit,
            )

    def photo(self, row: CommunityReferencePhoto, category: str | None = None) -> PhotoCandidate:
        return PhotoCandidate(
            provider=self.name,
            provider_asset_id=archive_identity(row),
            source_page_url=f"https://vk.com/photo{row.vk_photo_owner_id}_{row.vk_photo_id}",
            candidate_download_url="",
            width=row.width or 1,
            height=row.height or 1,
            license_code="community-opt-in",
            license_name="Explicit same-community archive policy",
            license_url="https://vk.com",
            # These labels express the configured community/grid taxonomy,
            # not inferred objects or OCR labels from the photograph.
            tags=tuple(PhotoQueryBuilder().build(category).variants[0].query.split())
            if category
            else (),
        )

    async def prepare(self, row: CommunityReferencePhoto) -> CommunityReferencePhoto:
        lock = self.locks.setdefault(row.id, asyncio.Lock())
        async with lock:
            service = self.collector
            # A second request may have queued with a stale detached row while
            # the first prepared it. Read the current cache under the local lock.
            async with service.sessions() as session:
                fresh_cache = await session.get(CommunityReferencePhoto, row.id)
            if fresh_cache is None or not fresh_cache.enabled:
                raise PhotoError("archive_photo_unavailable")
            row = fresh_cache
            if service.embedder is None:
                raise PhotoError("visual_embedding_disabled")
            data = None
            if row.storage_key:
                try:
                    path = service.storage.path(row.storage_key)
                    if path.is_file() and path.stat().st_size <= service.policy.max_input_bytes:
                        raw = await asyncio.to_thread(path.read_bytes)
                        if hashlib.sha256(raw).hexdigest() == row.sha256:
                            data = raw
                except (OSError, PhotoError):
                    pass
            if data is None:
                # A saved CDN capability is not trusted forever. Resolve the stable
                # post/photo identity with a fresh DB credential before downloading.
                async with service.sessions() as s:
                    c = await get_entity(s, Community, row.community_id)
                    aid = await s.scalar(
                        select(Account.id)
                        .where(
                            Account.status == AccountStatus.active,
                            Account.vk_user_id.is_not(None),
                            Account.encrypted_access_token.is_not(None),
                        )
                        .order_by(Account.created_at, Account.id)
                        .limit(1)
                    )
                if aid is None or not c.vk_group_id:
                    raise PhotoError("vk_credentials_unavailable")
                token = await service.tokens.get_token(aid)
                response = await service.client.get_wall_post_by_id(
                    -c.vk_group_id, row.vk_post_id, access_token=token, account_id=aid
                )
                photo = next(
                    (
                        p
                        for item in response.items
                        if (
                            p := representative_photo(
                                item.model_dump(), c.vk_group_id, service.policy
                            )
                        )
                        and p.owner_id == row.vk_photo_owner_id
                        and p.photo_id == row.vk_photo_id
                    ),
                    None,
                )
                if photo is None:
                    raise PhotoError("archive_photo_unavailable")
                raw = await service.downloader.download(photo.url)
                with stage("normalization"):
                    image = await asyncio.to_thread(normalize_image, raw, service.policy)
                key = await asyncio.to_thread(service.storage.write, image)
                self.download_count += 1
                row.storage_key, row.sha256, row.perceptual_hash = (
                    key,
                    image.sha256,
                    image.perceptual_hash,
                )
                row.width, row.height, row.source_url = image.width, image.height, photo.url
                row.posted_at = photo.posted_at
                row.embedding = row.embedding_model = row.embedding_dimensions = None
                data = image.data
            if not row.width or not row.height:
                import io

                from PIL import Image

                with Image.open(io.BytesIO(data)) as decoded:
                    row.width, row.height = decoded.size
            if not PhotoPolicy().dimensions_allowed(row.width, row.height):
                raise PhotoError("image_dimensions_rejected")
            valid = False
            if (
                row.embedding
                and row.embedding_model == service.embedder.model
                and row.embedding_dimensions == service.embedder.dimensions
            ):
                try:
                    deserialize_embedding(
                        row.embedding, row.embedding_model, row.embedding_dimensions
                    )
                    valid = True
                except PhotoError:
                    pass
            if not valid:
                vector = await service.embedder.embed_image(data)
                row.embedding, row.embedding_model, row.embedding_dimensions = (
                    serialize_embedding(vector),
                    vector.model,
                    vector.dimensions,
                )
                self.embedding_count += 1
            async with service.sessions() as s, s.begin():
                fresh = await s.get(CommunityReferencePhoto, row.id, with_for_update=True)
                if not fresh or not fresh.enabled:
                    raise PhotoError("archive_photo_unavailable")
                for name in (
                    "posted_at",
                    "storage_key",
                    "sha256",
                    "perceptual_hash",
                    "width",
                    "height",
                    "source_url",
                    "embedding",
                    "embedding_model",
                    "embedding_dimensions",
                ):
                    setattr(fresh, name, getattr(row, name))
            return row
