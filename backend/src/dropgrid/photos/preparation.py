"""Read-only media-context warmup; PostgreSQL queue, no post selection or sending."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.api.schemas import Input, Output
from dropgrid.db.models import (
    Campaign,
    Community,
    CommunityContentProfile,
    CommunityReferencePhoto,
    Grid,
    GridCommunity,
    MediaPreparationJob,
    Submission,
    utcnow,
)
from dropgrid.integrations.vk.errors import (
    VKAuthenticationError,
    VKCaptchaRequiredError,
    VKCredentialUnavailableError,
    VKError,
)
from dropgrid.photos.domain import PhotoError
from dropgrid.photos.references import CommunityReferenceCollector
from dropgrid.photos.timings import timed
from dropgrid.photos.visual import deserialize_embedding
from dropgrid.services.catalog import ConflictError, NotFoundError, get_entity
from dropgrid.services.community_resolution import apply_resolution
from dropgrid.services.vk_accounts import enabled_account


class PreparationInput(Input):
    account_id: UUID
    community_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=1000)
    retry_failed: bool = False


class MediaContext(Output):
    grid_id: UUID
    community_id: UUID
    resolution_status: str
    category: str | None = None
    comment: str | None = None
    content_hint: str | None = None
    references_ready: bool = False
    reference_count: int = 0
    reference_target: int
    archive_optional: bool = True
    archive_reuse_enabled: bool = False
    archive_indexed: bool = False
    media_context_ready: bool = False
    warnings: list[str] = Field(default_factory=list)


class GridReadiness(Output):
    total: int = 0
    resolved: int = 0
    active_resolvable: int = 0
    unavailable: int = 0
    not_found: int = 0
    deactivated: int = 0
    private: int = 0
    transient: int = 0
    unresolved: int = 0
    with_comment: int = 0
    with_content_hint: int = 0
    with_visual_references: int = 0
    references_ready: int = 0
    with_archive_indexed: int = 0
    archive_reuse_enabled: int = 0
    archive_optional: bool = True
    media_context_ready: int = 0
    ready_to_create_campaign: bool = False
    reference_warmup_target: int
    preparation_states: dict[str, int] = Field(default_factory=dict)


class PreparationJobRead(Output):
    id: UUID
    community_id: UUID
    account_id: UUID
    state: str
    attempts: int
    error_code: str | None
    result: dict[str, object] | None


class MediaPreparation:
    def __init__(self, collector: CommunityReferenceCollector) -> None:
        self.collector = collector
        self.sessions = collector.sessions
        self.settings = collector.client.settings
        self.target = self.settings.campaign_reference_warmup_target
        # Across concurrent API calls and worker jobs in this process, separate
        # from the account limiter, image semaphore and the CLIP embedder lock.
        self.context_slots = asyncio.Semaphore(self.settings.media_preparation_concurrency)

    async def _reference_counts(self, session: AsyncSession) -> dict[UUID, int]:
        embedder = self.collector.embedder
        if embedder is None:
            return {}
        rows = (
            await session.execute(
                select(CommunityReferencePhoto.community_id, func.count())
                .where(
                    CommunityReferencePhoto.is_style_reference.is_(True),
                    CommunityReferencePhoto.enabled.is_(True),
                    CommunityReferencePhoto.posted_at
                    >= utcnow() - timedelta(days=self.settings.campaign_reference_recent_days),
                    CommunityReferencePhoto.embedding_model == embedder.model,
                    CommunityReferencePhoto.embedding_dimensions == embedder.dimensions,
                    CommunityReferencePhoto.embedding.is_not(None),
                    CommunityReferencePhoto.storage_key.is_not(None),
                )
                .group_by(CommunityReferencePhoto.community_id)
            )
        ).all()
        return {cid: count for cid, count in rows}

    @timed("reference_loading")
    async def compatible_count(self, community_id: UUID) -> int:
        embedder = self.collector.embedder
        if embedder is None:
            return 0
        async with self.sessions() as s:
            rows = (
                await s.scalars(
                    select(CommunityReferencePhoto).where(
                        CommunityReferencePhoto.community_id == community_id,
                        CommunityReferencePhoto.is_style_reference.is_(True),
                        CommunityReferencePhoto.enabled.is_(True),
                        CommunityReferencePhoto.posted_at
                        >= utcnow() - timedelta(days=self.settings.campaign_reference_recent_days),
                        CommunityReferencePhoto.embedding_model == embedder.model,
                        CommunityReferencePhoto.embedding_dimensions == embedder.dimensions,
                        CommunityReferencePhoto.storage_key.is_not(None),
                    )
                )
            ).all()
        count = 0
        for row in rows:
            try:
                if (
                    row.embedding
                    and row.storage_key
                    and self.collector.storage.path(row.storage_key).is_file()
                ):
                    deserialize_embedding(row.embedding, embedder.model, embedder.dimensions)
                    count += 1
            except (PhotoError, OSError):
                continue
        return count

    async def context(self, grid_id: UUID, community_id: UUID) -> MediaContext:
        async with self.sessions() as s:
            relation = await s.get(GridCommunity, (grid_id, community_id))
            if relation is None:
                raise NotFoundError("Grid community not found")
            row = await get_entity(s, Community, community_id)
            profile = await s.get(CommunityContentProfile, community_id)
            archive_count = await s.scalar(
                select(func.count())
                .select_from(CommunityReferencePhoto)
                .where(
                    CommunityReferencePhoto.community_id == community_id,
                    CommunityReferencePhoto.archive_discovered.is_(True),
                )
            )
            result = MediaContext(
                grid_id=grid_id,
                community_id=community_id,
                resolution_status=row.resolution_status,
                category=relation.category,
                comment=relation.comment,
                content_hint=relation.content_hint,
                reference_target=self.target,
                archive_reuse_enabled=bool(profile and profile.archive_reuse_enabled),
                archive_indexed=bool(archive_count),
            )
            active = row.is_active and bool(row.vk_group_id) and row.resolution_status == "resolved"
        result.reference_count = await self.compatible_count(community_id)
        result.references_ready = result.reference_count >= self.target
        result.media_context_ready = active and result.references_ready
        return result

    async def prepare_community_media_context(
        self, grid_id: UUID, community_id: UUID, account_id: UUID
    ) -> MediaContext:
        async with self.context_slots:
            context = await self.context(grid_id, community_id)
            async with self.sessions() as s:
                await enabled_account(s, account_id)
                row = await get_entity(s, Community, community_id)
                domain, active = row.domain, row.is_active
            if not active:
                context.warnings = ["community_disabled"]
                return context
            if context.resolution_status != "resolved":
                token = await self.collector.tokens.get_token(account_id)
                result = (
                    await self.collector.client.resolve_communities(
                        [domain], access_token=token, account_id=account_id
                    )
                )[0]
                async with self.sessions() as s, s.begin():
                    await apply_resolution(s, community_id, result)
                context = await self.context(grid_id, community_id)
            if context.resolution_status != "resolved":
                context.warnings = ["community_" + context.resolution_status]
                return context
            if not context.references_ready:
                report = await self.collector.sync(
                    community_id,
                    account_id,
                    self.target,
                    max_scanned_posts=100,
                    timeout_seconds=120,
                    recent_since=utcnow()
                    - timedelta(days=self.settings.campaign_reference_recent_days),
                )
                context = await self.context(grid_id, community_id)
                context.warnings = report.warnings
            # Already-indexed archive stays optional; never discover/scan here.
            return context

    async def readiness(self, grid_id: UUID) -> GridReadiness:
        async with self.sessions() as s:
            await get_entity(s, Grid, grid_id)
            rows = (
                await s.execute(
                    select(Community, GridCommunity, CommunityContentProfile)
                    .join(GridCommunity, Community.id == GridCommunity.community_id)
                    .outerjoin(
                        CommunityContentProfile,
                        Community.id == CommunityContentProfile.community_id,
                    )
                    .where(GridCommunity.grid_id == grid_id)
                )
            ).all()
            counts = await self._reference_counts(s)
            archived = set(
                (
                    await s.scalars(
                        select(CommunityReferencePhoto.community_id)
                        .where(CommunityReferencePhoto.archive_discovered.is_(True))
                        .distinct()
                    )
                ).all()
            )
            state_rows = (
                await s.execute(
                    select(MediaPreparationJob.state, func.count())
                    .where(MediaPreparationJob.grid_id == grid_id)
                    .group_by(MediaPreparationJob.state)
                )
            ).all()
            states = {state: count for state, count in state_rows}
        result = GridReadiness(
            total=len(rows), reference_warmup_target=self.target, preparation_states=states
        )
        for community, relation, profile in rows:
            status = community.resolution_status
            result.resolved += int(status == "resolved")
            active = status == "resolved" and community.is_active and bool(community.vk_group_id)
            result.active_resolvable += int(active)
            result.not_found += int(status == "not_found")
            result.deactivated += int(status == "deactivated")
            result.private += int(status == "private_or_unavailable")
            result.transient += int(status == "transient_error")
            result.unresolved += int(status == "unresolved")
            result.unavailable += int(
                status in {"not_found", "deactivated", "private_or_unavailable"}
                or not community.is_active
            )
            result.with_comment += int(bool(relation.comment))
            result.with_content_hint += int(bool(relation.content_hint))
            count = counts.get(community.id, 0)
            result.with_visual_references += int(count > 0)
            result.references_ready += int(count >= self.target)
            result.with_archive_indexed += int(community.id in archived)
            result.archive_reuse_enabled += int(bool(profile and profile.archive_reuse_enabled))
            result.media_context_ready += int(active and count >= self.target)
        # Readiness counts describe metadata, not file integrity. Individual prep
        # rechecks embedding serialization/files before skipping any sync.
        result.ready_to_create_campaign = (
            bool(result.active_resolvable)
            and result.unresolved == 0
            and result.transient == 0
            and result.media_context_ready == result.active_resolvable
        )
        return result

    async def enqueue(self, grid_id: UUID, data: PreparationInput) -> int:
        async with self.sessions() as s, s.begin():
            await get_entity(s, Grid, grid_id)
            await enabled_account(s, data.account_id)
            relation_rows = (
                await s.execute(
                    select(GridCommunity, Community.resolution_status)
                    .join(Community, Community.id == GridCommunity.community_id)
                    .where(GridCommunity.grid_id == grid_id)
                )
            ).all()
            relations = {
                relation.community_id: (relation, status) for relation, status in relation_rows
            }
            members = set(relations)
            counts = await self._reference_counts(s)
            existing_jobs = {
                job.community_id: job
                for job in (
                    await s.scalars(
                        select(MediaPreparationJob).where(
                            MediaPreparationJob.grid_id == grid_id,
                            MediaPreparationJob.account_id == data.account_id,
                        )
                    )
                ).all()
            }
            selected = set(data.community_ids) if data.community_ids is not None else members
            if not selected <= members:
                raise ConflictError("Preparation community is not in grid")
            for cid in sorted(selected):
                statement = insert(MediaPreparationJob).values(
                    grid_id=grid_id, community_id=cid, account_id=data.account_id
                )
                relation, status = relations[cid]
                old = existing_jobs.get(cid)
                snapshot = old.result if old and old.result else {}
                stale = (
                    counts.get(cid, 0) < self.target
                    or status != "resolved"
                    or any(
                        snapshot.get(field) != getattr(relation, field)
                        for field in ("category", "comment", "content_hint")
                    )
                )
                retry_states = ["failed", "transient"] if data.retry_failed else []
                if stale:
                    retry_states.append("ready")
                statement = statement.on_conflict_do_update(
                    constraint="uq_media_prep_context",
                    set_={
                        "state": "queued",
                        "error_code": None,
                        "result": None,
                        "updated_at": utcnow(),
                    },
                    where=MediaPreparationJob.state.in_(retry_states),
                )
                await s.execute(statement)
        return len(selected)

    async def enqueue_campaign(self, campaign_id: UUID, data: PreparationInput) -> int:
        async with self.sessions() as s:
            campaign = await get_entity(s, Campaign, campaign_id)
            ids = list(
                (
                    await s.scalars(
                        select(Submission.community_id).where(Submission.campaign_id == campaign_id)
                    )
                ).all()
            )
            grid_id = campaign.grid_id
        if not ids:
            raise ConflictError("Prepare campaign submissions first")
        if data.community_ids is not None and not set(data.community_ids) <= set(ids):
            raise ConflictError("Preparation community is not in campaign")
        return await self.enqueue(
            grid_id, data.model_copy(update={"community_ids": data.community_ids or ids})
        )

    async def claim(self) -> tuple[UUID, UUID] | None:
        async with self.sessions() as s, s.begin():
            job = await s.scalar(
                select(MediaPreparationJob)
                .where(
                    or_(
                        MediaPreparationJob.state == "queued",
                        (MediaPreparationJob.state == "running")
                        & (MediaPreparationJob.lease_until < utcnow()),
                    )
                )
                .order_by(MediaPreparationJob.created_at, MediaPreparationJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.state, job.lease_token = "running", uuid4()
            job.lease_until = utcnow() + timedelta(minutes=6)
            job.attempts += 1
            return job.id, job.lease_token

    async def run_one(self) -> bool:
        claimed = await self.claim()
        if claimed is None:
            return False
        jid, lease = claimed
        async with self.sessions() as s:
            job = await s.get(MediaPreparationJob, jid)
            assert job is not None
            gid, cid, aid = job.grid_id, job.community_id, job.account_id
        result = None
        error = None
        state = "transient"
        try:
            async with asyncio.timeout(150):
                context = await self.prepare_community_media_context(gid, cid, aid)
            result = context.model_dump(mode="json")
            state = (
                "ready"
                if context.media_context_ready
                else (
                    "failed"
                    if context.resolution_status
                    in {"not_found", "deactivated", "private_or_unavailable"}
                    or "community_disabled" in context.warnings
                    else "transient"
                )
            )
            if state != "ready":
                error = "media_context_incomplete"
        except (VKAuthenticationError, VKCaptchaRequiredError, VKCredentialUnavailableError):
            state, error = "failed", "vk_account_unavailable"
        except (VKError, PhotoError, OSError, TimeoutError):
            error = "media_preparation_temporary_failure"
        except (NotFoundError, ConflictError):
            error = "media_preparation_conflict"
        async with self.sessions() as s, s.begin():
            current = await s.get(MediaPreparationJob, jid, with_for_update=True)
            if current and current.lease_token == lease:
                current.state, current.result, current.error_code = state, result, error
                current.lease_token = current.lease_until = None
        return True

    async def tick(self) -> int:
        # Fixed number of lanes, never a task per grid member. Safe independent claims.
        return sum(
            await asyncio.gather(
                *(self.run_one() for _ in range(self.settings.media_preparation_concurrency))
            )
        )
