"""Bounded read-only VK photo-reference collection, separate from candidate media."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import (
    Account,
    Community,
    CommunityContentProfile,
    CommunityReferencePhoto,
    utcnow,
)
from dropgrid.domain.enums import AccountStatus
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.errors import VKError
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.photos.density import refresh_density
from dropgrid.photos.domain import PhotoError, PhotoPolicy
from dropgrid.photos.download import PhotoDownloader, validate_url
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.photos.reference_schemas import ProfileInput, ProfileRead, ReferenceSyncRead
from dropgrid.photos.timings import stage
from dropgrid.photos.visual import VisualEmbedder, deserialize_embedding, serialize_embedding
from dropgrid.services.catalog import ConflictError, get_entity

MAX_SCANNED_POSTS = 400
SYNC_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class VKReferencePolicy(PhotoPolicy):
    min_short_side: int = 128
    max_aspect_ratio: float = 20
    trusted_hosts: frozenset[str] = frozenset(
        {"userapi.com", "vkuserphoto.ru", "vkuserphoto.com", "vk-cdn.net"}
    )

    def trusted_host(self, host: str) -> bool:
        return any(host == root or host.endswith("." + root) for root in self.trusted_hosts)


def eligible_for_archive_reuse(
    posted_at: datetime,
    now: datetime,
    min_age_days: int,
    policy_enabled: bool,
    valid: bool = True,
    max_age_days: int | None = None,
) -> bool:
    if (
        not policy_enabled
        or not valid
        or type(min_age_days) is not int
        or min_age_days < 0
        or (
            max_age_days is not None
            and (type(max_age_days) is not int or max_age_days <= min_age_days)
        )
        or posted_at.tzinfo is None
        or now.tzinfo is None
        or posted_at > now
    ):
        return False
    age = (now - posted_at).total_seconds() / 86400
    return age >= min_age_days and (max_age_days is None or age <= max_age_days)


async def profile_read(session: AsyncSession, community_id: UUID) -> ProfileRead:
    await get_entity(session, Community, community_id)
    profile = await session.get(CommunityContentProfile, community_id)
    from sqlalchemy import func

    count = await session.scalar(
        select(func.count())
        .select_from(CommunityReferencePhoto)
        .where(
            CommunityReferencePhoto.community_id == community_id,
            CommunityReferencePhoto.is_style_reference.is_(True),
        )
    )
    data = (
        ProfileRead.model_validate(profile) if profile else ProfileRead(community_id=community_id)
    )
    now = utcnow()
    archive = CommunityReferencePhoto
    discovered = await session.scalar(
        select(func.count())
        .select_from(archive)
        .where(archive.community_id == community_id, archive.archive_discovered.is_(True))
    )
    lower, upper = (
        now - timedelta(days=data.archive_reuse_max_age_days),
        now - timedelta(days=data.archive_reuse_min_age_days),
    )
    eligible, oldest, newest = (
        await session.execute(
            select(func.count(), func.min(archive.posted_at), func.max(archive.posted_at)).where(
                archive.community_id == community_id,
                archive.archive_discovered.is_(True),
                archive.enabled.is_(True),
                archive.vk_photo_owner_id != 0,
                archive.vk_photo_id > 0,
                archive.width >= PhotoPolicy().min_short_side,
                archive.height >= PhotoPolicy().min_short_side,
                archive.width <= archive.height * PhotoPolicy().max_aspect_ratio,
                archive.height <= archive.width * PhotoPolicy().max_aspect_ratio,
                archive.posted_at >= lower,
                archive.posted_at <= upper,
            )
        )
    ).one()
    return data.model_copy(
        update={
            "reference_count": count or 0,
            "archive_discovered_count": discovered or 0,
            "archive_eligible_count": eligible if data.archive_reuse_enabled else 0,
            "archive_oldest_eligible_at": oldest if data.archive_reuse_enabled else None,
            "archive_newest_eligible_at": newest if data.archive_reuse_enabled else None,
        }
    )


async def profile_save(
    session: AsyncSession, community_id: UUID, data: ProfileInput
) -> ProfileRead:
    await get_entity(session, Community, community_id)
    locked = await session.get(CommunityContentProfile, community_id, with_for_update=True)
    if locked and any(
        until and until > utcnow()
        for until in (
            locked.sync_lease_until,
            locked.archive_lease_until,
            locked.preview_lease_until,
        )
    ):
        raise PhotoConflict("profile_locked")
    await session.execute(
        insert(CommunityContentProfile)
        .values(community_id=community_id, **data.model_dump())
        .on_conflict_do_update(
            index_elements=["community_id"], set_={**data.model_dump(), "updated_at": utcnow()}
        )
    )
    await session.flush()
    if locked:
        await session.refresh(locked)
    # Recompute eligibility when policy changes; no conversion/republication.
    rows = (
        await session.scalars(
            select(CommunityReferencePhoto).where(
                CommunityReferencePhoto.community_id == community_id
            )
        )
    ).all()
    for row in rows:
        row.reuse_eligible = eligible_for_archive_reuse(
            row.posted_at,
            utcnow(),
            data.archive_reuse_min_age_days,
            data.archive_reuse_enabled,
            bool(row.enabled and row.vk_photo_id > 0 and row.vk_photo_owner_id),
            data.archive_reuse_max_age_days,
        )
    return await profile_read(session, community_id)


@dataclass(frozen=True)
class ExtractedPhoto:
    post_id: int
    owner_id: int
    photo_id: int
    posted_at: datetime
    url: str
    width: int
    height: int


def representative_photo(
    post: dict[str, object], group: int, policy: PhotoPolicy
) -> ExtractedPhoto | None:
    if (
        post.get("owner_id") != -group
        or post.get("from_id") != -group
        or post.get("post_type") != "post"
        or post.get("copy_history")
    ):
        return None
    identity, date = post.get("id"), post.get("date")
    if type(identity) is not int or identity <= 0 or type(date) is not int or date <= 0:
        return None
    try:
        posted_at = datetime.fromtimestamp(date, UTC)
    except (ValueError, OverflowError, OSError):
        return None
    raw = post.get("attachments")
    if not isinstance(raw, list):
        return None
    for attachment in raw:
        if not isinstance(attachment, dict) or attachment.get("type") != "photo":
            continue
        photo = attachment.get("photo")
        if not isinstance(photo, dict):
            continue
        owner, pid = photo.get("owner_id"), photo.get("id")
        if type(owner) is not int or owner == 0 or type(pid) is not int or pid <= 0:
            continue
        sizes = photo.get("sizes")
        if not isinstance(sizes, list):
            continue
        valid = []
        for size in sizes:
            if not isinstance(size, dict):
                continue
            w, h, url = size.get("width"), size.get("height"), size.get("url")
            if (
                type(w) is not int
                or type(h) is not int
                or w <= 0
                or h <= 0
                or w * h > policy.max_pixels
                or not policy.dimensions_allowed(w, h)
                or not isinstance(url, str)
            ):
                continue
            try:
                validate_url(url, policy)
            except PhotoError:
                continue
            valid.append((w * h, w, h, url))
        if valid:
            # Primary attachment, largest suitable size; URL tie-break stays in memory.
            chosen = max(valid)
            return ExtractedPhoto(identity, owner, pid, posted_at, chosen[3], chosen[1], chosen[2])
    return None


class CommunityReferenceCollector:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: VKClient,
        tokens: TokenProvider,
        downloader: PhotoDownloader,
        storage: LocalMediaStorage,
        embedder: VisualEmbedder | None,
    ) -> None:
        self.sessions, self.client, self.tokens = sessions, client, tokens
        self.downloader, self.storage, self.embedder = downloader, storage, embedder
        self.policy = VKReferencePolicy()

    async def _embed_existing(self, row: CommunityReferencePhoto) -> bool:
        if self.embedder is None or not row.storage_key:
            return False
        if (
            row.embedding
            and row.embedding_model == self.embedder.model
            and row.embedding_dimensions == self.embedder.dimensions
        ):
            try:
                deserialize_embedding(row.embedding, row.embedding_model, row.embedding_dimensions)
                return False
            except PhotoError:
                pass
        import hashlib

        path = self.storage.path(row.storage_key)
        if not path.is_file():
            raise PhotoError("reference_file_unavailable")
        if path.stat().st_size > self.policy.max_input_bytes:
            raise PhotoError("image_too_large")
        data = await asyncio.to_thread(path.read_bytes)
        if hashlib.sha256(data).hexdigest() != row.sha256:
            raise PhotoError("reference_file_unavailable")
        embedding = await self.embedder.embed_image(data)
        async with self.sessions() as session, session.begin():
            fresh = await session.get(CommunityReferencePhoto, row.id, with_for_update=True)
            if fresh and fresh.sha256 == row.sha256:
                fresh.embedding = serialize_embedding(embedding)
                fresh.embedding_model, fresh.embedding_dimensions = (
                    embedding.model,
                    embedding.dimensions,
                )
        return True

    async def sync(
        self,
        community_id: UUID,
        account_id: UUID | None = None,
        target_count: int | None = None,
        *,
        max_scanned_posts: int = MAX_SCANNED_POSTS,
        timeout_seconds: float = SYNC_TIMEOUT_SECONDS,
        recent_since: datetime | None = None,
        progress: Callable[[str, ReferenceSyncRead], Awaitable[None]] | None = None,
    ) -> ReferenceSyncRead:
        if (
            not 1 <= max_scanned_posts <= MAX_SCANNED_POSTS
            or not 0 < timeout_seconds <= SYNC_TIMEOUT_SECONDS
        ):
            raise ConflictError("Invalid reference sync bounds")
        lease = uuid4()
        report = ReferenceSyncRead()
        finished = False
        async with self.sessions() as session, session.begin():
            community = await get_entity(session, Community, community_id)
            if not community.vk_group_id or not community.is_active:
                raise ConflictError("Resolve an active Community first")
            group = community.vk_group_id
            await session.execute(
                insert(CommunityContentProfile)
                .values(community_id=community_id)
                .on_conflict_do_nothing()
            )
            profile = await session.get(CommunityContentProfile, community_id, with_for_update=True)
            assert profile is not None
            for until, code in (
                (profile.sync_lease_until, "reference_sync_in_progress"),
                (profile.archive_lease_until, "archive_sync_in_progress"),
                (profile.preview_lease_until, "preview_in_progress"),
            ):
                if until and until > utcnow():
                    raise PhotoConflict(code)
            profile.sync_lease_token, profile.sync_lease_until = (
                lease,
                utcnow() + timedelta(minutes=6),
            )
            target = target_count if target_count is not None else profile.reference_target_count
            if type(target) is not int or not 1 <= target <= 300:
                raise ConflictError("Invalid reference target")
            enabled, age = profile.archive_reuse_enabled, profile.archive_reuse_min_age_days
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
            aid = await session.scalar(query.limit(1))

        async def notify(state: str) -> None:
            if progress:
                await progress(state, report)

        seen: set[tuple[int, int]] = set()
        offset = 0
        try:
            if aid is None:
                report.warnings.append("vk_credentials_unavailable")
                return report
            token = await self.tokens.get_token(aid)
            async with asyncio.timeout(timeout_seconds):
                while report.posts_scanned < max_scanned_posts and len(seen) < target:
                    await notify("reading_wall")
                    page = await self.client.get_community_wall_history(
                        group,
                        access_token=token,
                        account_id=aid,
                        count=min(100, max_scanned_posts - report.posts_scanned),
                        offset=offset,
                    )
                    if not page.items:
                        finished = True
                        break
                    for post in page.items[: max_scanned_posts - report.posts_scanned]:
                        report.posts_scanned += 1
                        offset += 1
                        photo = representative_photo(post, group, self.policy)
                        if not photo or recent_since and photo.posted_at < recent_since:
                            continue
                        identity = (photo.owner_id, photo.photo_id)
                        if identity in seen:
                            continue
                        report.photo_posts_found += 1
                        async with self.sessions() as session:
                            existing = await session.scalar(
                                select(CommunityReferencePhoto).where(
                                    CommunityReferencePhoto.community_id == community_id,
                                    CommunityReferencePhoto.vk_photo_owner_id == photo.owner_id,
                                    CommunityReferencePhoto.vk_photo_id == photo.photo_id,
                                )
                            )
                        has_local_file = False
                        if existing and existing.storage_key:
                            try:
                                has_local_file = self.storage.path(existing.storage_key).is_file()
                            except PhotoError:
                                pass
                        if existing and has_local_file:
                            async with self.sessions() as session, session.begin():
                                fresh = await session.get(CommunityReferencePhoto, existing.id)
                                if fresh:
                                    fresh.is_style_reference = True
                            report.references_existing += 1
                            seen.add(identity)
                            try:
                                await notify("embedding")
                                report.references_embedded += int(
                                    await self._embed_existing(existing)
                                )
                                report.embeddings_available += int(self.embedder is not None)
                            except PhotoError as e:
                                report.warnings.append(e.code)
                        else:
                            try:
                                await notify("downloading")
                                raw = await self.downloader.download(photo.url)
                                with stage("normalization"):
                                    image = await asyncio.to_thread(
                                        normalize_image, raw, self.policy
                                    )
                                key = await asyncio.to_thread(self.storage.write, image)
                                report.downloads_succeeded += 1
                                embedding = None
                                if self.embedder:
                                    try:
                                        await notify("embedding")
                                        embedding = await self.embedder.embed_image(image.data)
                                    except PhotoError as e:
                                        report.warnings.append(e.code)
                                async with self.sessions() as session, session.begin():
                                    p = await session.get(
                                        CommunityContentProfile, community_id, with_for_update=True
                                    )
                                    if (
                                        not p
                                        or p.sync_lease_token != lease
                                        or p.sync_lease_until is None
                                        or p.sync_lease_until <= utcnow()
                                    ):
                                        raise ConflictError("Reference sync lease expired")
                                    collected = CommunityReferencePhoto(
                                        community_id=community_id,
                                        vk_post_id=photo.post_id,
                                        vk_photo_owner_id=photo.owner_id,
                                        vk_photo_id=photo.photo_id,
                                        posted_at=photo.posted_at,
                                        width=image.width,
                                        height=image.height,
                                        source_url=photo.url,
                                        storage_key=key,
                                        sha256=image.sha256,
                                        perceptual_hash=image.perceptual_hash,
                                        embedding=serialize_embedding(embedding)
                                        if embedding
                                        else None,
                                        embedding_model=embedding.model if embedding else None,
                                        embedding_dimensions=embedding.dimensions
                                        if embedding
                                        else None,
                                        reuse_eligible=eligible_for_archive_reuse(
                                            photo.posted_at,
                                            utcnow(),
                                            age,
                                            enabled,
                                            max_age_days=profile.archive_reuse_max_age_days,
                                        ),
                                    )
                                    if existing:
                                        for name in (
                                            "vk_post_id",
                                            "posted_at",
                                            "width",
                                            "height",
                                            "source_url",
                                            "storage_key",
                                            "sha256",
                                            "perceptual_hash",
                                            "embedding",
                                            "embedding_model",
                                            "embedding_dimensions",
                                            "reuse_eligible",
                                        ):
                                            setattr(existing, name, getattr(collected, name))
                                        existing.is_style_reference = True
                                        session.add(existing)
                                    else:
                                        session.add(collected)
                                report.references_created += int(existing is None)
                                report.references_existing += int(existing is not None)
                                report.references_embedded += int(embedding is not None)
                                report.embeddings_available += int(embedding is not None)
                                seen.add(identity)
                            except PhotoError as e:
                                report.warnings.append(e.code)
                            except OSError:
                                report.warnings.append("reference_storage_unavailable")
                        await notify("reading_wall")
                        if len(seen) >= target:
                            finished = True
                            break
                    if len(seen) >= target or offset >= page.count:
                        finished = True
                        break
                if report.posts_scanned >= max_scanned_posts:
                    report.warnings.append("scan_bound_reached")
        except VKError as e:
            report.warnings.append("vk_read_" + type(e).__name__)
        except TimeoutError:
            report.warnings.append("sync_timeout")
        finally:
            async with self.sessions() as session, session.begin():
                profile = await session.get(
                    CommunityContentProfile, community_id, with_for_update=True
                )
                if profile and profile.sync_lease_token == lease:
                    if finished:
                        profile.references_last_synced_at = utcnow()
                    profile.sync_lease_token = profile.sync_lease_until = None
            if self.embedder is None:
                report.warnings.append("visual_embedding_disabled")
            report.warnings = sorted(set(report.warnings))
        await notify("finalizing")
        await refresh_density(self.sessions, community_id, self.embedder)
        return report

    async def backfill(self, community_id: UUID) -> int:
        async with self.sessions() as session:
            await get_entity(session, Community, community_id)
            rows = (
                await session.scalars(
                    select(CommunityReferencePhoto)
                    .where(CommunityReferencePhoto.community_id == community_id)
                    .order_by(CommunityReferencePhoto.id)
                    .limit(300)
                )
            ).all()
        count = sum([int(await self._embed_existing(row)) for row in rows])
        await refresh_density(self.sessions, community_id, self.embedder)
        return count
