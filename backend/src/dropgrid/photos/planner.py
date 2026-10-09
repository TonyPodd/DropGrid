"""Campaign media planning; intentionally independent of VK authorization.

Durable campaign lease -> short snapshot -> bounded network -> short assignment.
Imports are serialized only for dedup persistence, never across HTTP downloads.
"""

import asyncio
import logging
from collections import Counter, defaultdict
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.config import Settings
from dropgrid.db.models import (
    Campaign,
    Community,
    CommunityContentProfile,
    GridCommunity,
    MediaAsset,
    MediaProviderImport,
    PhotoPlanLease,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import CampaignStatus, SubmissionStatus
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import (
    Deduplicator,
    PhotoCandidate,
    PhotoError,
    PhotoPolicy,
    PhotoQueryBuilder,
    PhotoRanker,
    normalize_category,
)
from dropgrid.photos.download import PhotoDownloader
from dropgrid.photos.images import MediaStorage, normalize_image
from dropgrid.photos.schemas import CategoryPlanRead, MediaPlanInput, MediaPlanRead
from dropgrid.photos.timings import stage
from dropgrid.photos.visual import serialize_embedding
from dropgrid.photos.visual_library import VisualLibrary
from dropgrid.services.campaigns import locked_campaign
from dropgrid.services.catalog import ConflictError

logger = logging.getLogger(__name__)


def assign_assets(
    submissions: list[UUID],
    assets: list[MediaAsset],
    counts: Counter[UUID],
    reuse: int,
    pending: Counter[UUID],
    visual_scores: dict[UUID, dict[UUID, float]] | None = None,
    allowed_assets: dict[UUID, set[UUID]] | None = None,
) -> dict[UUID, UUID]:
    """Unique first, balanced reuse, creator/history/current-reference penalties."""
    assigned: dict[UUID, UUID] = {}
    creators: Counter[str] = Counter()
    previous: UUID | None = None
    for submission in submissions:
        available = [
            a
            for a in assets
            if counts[a.id] < reuse
            and (allowed_assets is None or a.id in allowed_assets.get(submission, set()))
        ]
        if not available:
            break
        chosen = min(
            available,
            key=lambda a: (
                counts[a.id],
                -(visual_scores[submission].get(a.id, 0))
                if visual_scores and submission in visual_scores
                else 0,
                a.id == previous,
                creators[a.creator_name or ""],
                a.usage_count * 0.5 + pending[a.id] * 0.25 + (1 if a.last_used_at else 0),
                a.last_used_at.timestamp() if a.last_used_at else 0,
                -(min(a.width or 0, a.height or 0)),
                str(a.id),
            ),
        )
        assigned[submission] = chosen.id
        counts[chosen.id] += 1
        creators[chosen.creator_name or ""] += 1
        previous = chosen.id
    return assigned


class CampaignMediaPlanner:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        cache: SearchCache,
        downloader: PhotoDownloader,
        storage: MediaStorage,
        settings: Settings,
        policy: PhotoPolicy | None = None,
        visual: VisualLibrary | None = None,
    ) -> None:
        self.sessions, self.cache, self.downloader = sessions, cache, downloader
        self.storage, self.settings = storage, settings
        self.policy = policy or PhotoPolicy()
        self.visual = visual
        from dropgrid.photos.archive import VKArchivePhotoProvider

        self.archive: VKArchivePhotoProvider | None = None
        self.dedup = Deduplicator(self.policy.hamming_threshold)
        from dropgrid.photos.pinterest_preview import PinterestPreview

        self.pinterest_preview: PinterestPreview | None = None
        self.pinterest_status = "disabled"
        self.builder, self.ranker = PhotoQueryBuilder(), PhotoRanker()
        self.semaphore = asyncio.Semaphore(settings.photo_download_concurrency)

    def eligible(self, asset: MediaAsset, sensitive: bool = False) -> bool:
        if (
            asset.provider == "pinterest"
            or not asset.enabled
            or not asset.sha256
            or not asset.license_code
            or not asset.provider
            or not asset.width
            or not asset.height
        ):
            return False
        candidate = self._asset_candidate(asset)
        try:
            return (
                self.policy.candidate_allowed(candidate, sensitive)
                and self.storage.path(asset.storage_key).is_file()
            )
        except PhotoError:
            return False

    @staticmethod
    def _asset_candidate(asset: MediaAsset) -> PhotoCandidate:
        return PhotoCandidate(
            provider=asset.provider or "",
            provider_asset_id=asset.provider_asset_id or "",
            source_page_url=asset.source_url or "",
            candidate_download_url="",
            creator_name=asset.creator_name or "",
            width=asset.width or 1,
            height=asset.height or 1,
            tags=tuple(asset.tags),
            license_code=asset.license_code or "",
            license_name=asset.license_name or "",
            license_url=asset.license_url or "",
        )

    async def _import(
        self, candidate: PhotoCandidate, category: str, sensitive: bool
    ) -> tuple[MediaAsset | None, bool, str | None]:
        if candidate.provider == "pinterest" or not candidate.publication_eligible:
            return None, False, "publication_ineligible"
        async with self.semaphore:
            try:
                logger.info(
                    "photo.candidate.selected",
                    extra={"category": category, "provider": candidate.provider},
                )
                data = await self.downloader.download(candidate.candidate_download_url)
                with stage("normalization"):
                    image = await asyncio.to_thread(normalize_image, data, self.policy)
                embedding = None
                embedding_warning = None
                if self.visual and self.visual.embedder:
                    try:
                        embedding = await self.visual.embedder.embed_image(image.data)
                    except PhotoError as exc:
                        embedding_warning = exc.code
                async with self.sessions() as session, session.begin():
                    # Global import lock handles perceptual/provider/SHA races across campaigns.
                    await session.execute(text("SELECT pg_advisory_xact_lock(748220102)"))
                    existing = await session.scalar(
                        select(MediaAsset)
                        .outerjoin(
                            MediaProviderImport, MediaProviderImport.media_asset_id == MediaAsset.id
                        )
                        .where(
                            or_(
                                MediaAsset.sha256 == image.sha256,
                                (MediaAsset.provider == candidate.provider)
                                & (MediaAsset.provider_asset_id == candidate.provider_asset_id),
                                (MediaProviderImport.provider == candidate.provider)
                                & (
                                    MediaProviderImport.provider_asset_id
                                    == candidate.provider_asset_id
                                ),
                            )
                        )
                    )
                    if existing is None:
                        hashes = (
                            await session.scalars(
                                select(MediaAsset)
                                .where(MediaAsset.perceptual_hash.is_not(None))
                                .order_by(MediaAsset.id)
                            )
                        ).all()
                        existing = next(
                            (
                                a
                                for a in hashes
                                if self.dedup.near(a.perceptual_hash, image.perceptual_hash)
                            ),
                            None,
                        )
                    if existing is not None:
                        if (
                            not self.eligible(existing, sensitive)
                            or existing.provider != candidate.provider
                            or existing.license_code != candidate.license_code
                            or existing.requires_publication_attribution
                            != candidate.requires_publication_attribution
                        ):
                            return None, False, "duplicate_not_reusable"
                        asset = existing
                        created = False
                        logger.info(
                            "photo.asset.deduplicated", extra={"provider": candidate.provider}
                        )
                    else:
                        key = await asyncio.to_thread(self.storage.write, image)
                        asset = MediaAsset(
                            storage_key=key,
                            source_url=candidate.source_page_url,
                            category=category or None,
                            tags=list(candidate.tags),
                            provider=candidate.provider,
                            provider_asset_id=candidate.provider_asset_id,
                            creator_name=candidate.creator_name,
                            creator_url=candidate.creator_url,
                            license_code=candidate.license_code,
                            license_name=candidate.license_name,
                            license_url=candidate.license_url,
                            attribution_text=candidate.attribution_text,
                            requires_publication_attribution=candidate.requires_publication_attribution,
                            width=image.width,
                            height=image.height,
                            mime_type="image/jpeg",
                            byte_size=len(image.data),
                            sha256=image.sha256,
                            perceptual_hash=image.perceptual_hash,
                        )
                        session.add(asset)
                        await session.flush()
                        created = True
                        logger.info(
                            "photo.asset.downloaded",
                            extra={"category": category, "provider": candidate.provider},
                        )
                    await session.execute(
                        insert(MediaProviderImport)
                        .values(
                            provider=candidate.provider,
                            provider_asset_id=candidate.provider_asset_id,
                            media_asset_id=asset.id,
                            source_url=candidate.source_page_url,
                            creator_name=candidate.creator_name,
                            creator_url=candidate.creator_url,
                            license_code=candidate.license_code,
                        )
                        .on_conflict_do_nothing()
                    )
                    if embedding and asset.sha256 == image.sha256 and not asset.visual_embedding:
                        asset.visual_embedding = serialize_embedding(embedding)
                        asset.visual_embedding_model = embedding.model
                        asset.visual_embedding_dimensions = embedding.dimensions
                    return asset, created, embedding_warning
            except PhotoError as exc:
                return None, False, exc.code
            except OSError:
                return None, False, "storage_unavailable"

    async def plan(self, campaign_id: UUID, data: MediaPlanInput) -> MediaPlanRead:
        token = uuid4()
        async with self.sessions() as session, session.begin():
            campaign = await locked_campaign(session, campaign_id)
            if campaign.status != CampaignStatus.ready:
                raise ConflictError("Prepare campaign before selecting photos")
            await session.execute(
                insert(PhotoPlanLease)
                .values(
                    campaign_id=campaign_id,
                    token=token,
                    expires_at=utcnow() + timedelta(minutes=10),
                )
                .on_conflict_do_nothing()
            )
            lease = await session.get(PhotoPlanLease, campaign_id, with_for_update=True)
            assert lease is not None
            if lease.token != token and lease.expires_at > utcnow():
                raise ConflictError("Photo planning is already running for this campaign")
            lease.token, lease.expires_at = token, utcnow() + timedelta(minutes=10)
        logger.info("photo.plan.started", extra={"campaign_id": str(campaign_id)})
        try:
            return await self._plan(campaign_id, token, data)
        finally:
            async with self.sessions() as session, session.begin():
                from sqlalchemy import delete

                await session.execute(
                    delete(PhotoPlanLease).where(
                        PhotoPlanLease.campaign_id == campaign_id, PhotoPlanLease.token == token
                    )
                )

    async def _plan(self, campaign_id: UUID, token: UUID, data: MediaPlanInput) -> MediaPlanRead:
        async with self.sessions() as session, session.begin():
            campaign = await session.get(Campaign, campaign_id)
            assert campaign is not None
            rows = (
                await session.execute(
                    select(Submission, GridCommunity.category)
                    .join(Community, Community.id == Submission.community_id)
                    .join(
                        GridCommunity,
                        (GridCommunity.community_id == Submission.community_id)
                        & (GridCommunity.grid_id == campaign.grid_id),
                    )
                    .where(Submission.campaign_id == campaign_id)
                    .order_by(Community.domain, Submission.id)
                )
            ).all()
            library = list(
                (
                    await session.scalars(
                        select(MediaAsset)
                        .where(
                            MediaAsset.enabled.is_(True),
                            or_(MediaAsset.provider.is_(None), MediaAsset.provider != "vk_archive"),
                        )
                        .order_by(MediaAsset.id)
                    )
                ).all()
            )
            aliases = {
                (a.provider, a.provider_asset_id): a.media_asset_id
                for a in (await session.scalars(select(MediaProviderImport))).all()
            }
            pending: Counter[UUID] = Counter(
                {
                    asset_id: count
                    for asset_id, count in (
                        await session.execute(
                            select(Submission.media_asset_id, func.count())
                            .where(
                                Submission.media_asset_id.is_not(None),
                                Submission.status == SubmissionStatus.pending,
                            )
                            .group_by(Submission.media_asset_id)
                        )
                    ).all()
                    if asset_id is not None
                }
            )
        async with self.sessions() as session:
            hints = {
                r.community_id: r.content_hint
                for r in (
                    await session.scalars(
                        select(GridCommunity).where(GridCommunity.grid_id == campaign.grid_id)
                    )
                ).all()
            }
            desired = {
                p.community_id: p.desired_content
                for p in (
                    await session.scalars(
                        select(CommunityContentProfile).where(
                            CommunityContentProfile.community_id.in_(
                                [s.community_id for s, _ in rows]
                            )
                        )
                    )
                ).all()
            }
        total = len(rows)
        previously = sum(s.media_asset_id is not None for s, _ in rows)
        report = MediaPlanRead(
            campaign_id=campaign_id, total_submissions=total, previously_assigned=previously
        )
        groups: dict[tuple[str, str | None, str | None], list[UUID]] = defaultdict(list)
        all_groups: dict[str, list[UUID]] = defaultdict(list)
        for submission, category in rows:
            key = normalize_category(category)
            all_groups[key].append(submission.id)
            if submission.status == SubmissionStatus.pending and (
                data.force or not submission.media_asset_id
            ):
                groups[
                    (key, hints.get(submission.community_id), desired.get(submission.community_id))
                ].append(submission.id)
        available: dict[tuple[str, str | None, str | None], list[MediaAsset]] = {}
        categories: dict[str, CategoryPlanRead] = {
            key: CategoryPlanRead(name=key or "Без категории", submission_count=len(ids))
            for key, ids in sorted(all_groups.items())
        }
        imported_ids: set[UUID] = set()
        reuse = data.max_reuse_per_asset or self.settings.photo_max_reuse_per_asset
        preserved_counts: Counter[UUID] = Counter(
            s.media_asset_id
            for s, _ in rows
            if s.media_asset_id and not (data.force and s.status == SubmissionStatus.pending)
        )
        stop_provider: str | None = None
        try:
            async with asyncio.timeout(self.policy.plan_timeout_seconds):
                for context, submission_ids in sorted(
                    groups.items(), key=lambda item: str(item[0])
                ):
                    category, hint, wanted = context
                    plan = self.builder.build(category, hint, wanted)
                    from dropgrid.photos.concepts import concept_queries

                    hinted = bool(concept_queries(hint) or concept_queries(wanted))
                    assets = [
                        a
                        for a in library
                        if normalize_category(a.category) == category
                        and self.eligible(a, plan.sensitive)
                        and preserved_counts[a.id] < reuse
                    ]
                    available[context] = assets
                    target = (
                        len(assets) + min(4, len(submission_ids)) if hinted else len(submission_ids)
                    )
                    need = max(0, target - len(assets))
                    if not need:
                        continue
                    warnings = categories[category].warnings
                    if plan.sensitive and not self.cache.provider.supports_sensitive_context:
                        warnings.append("provider_context_restricted")
                        continue
                    if stop_provider:
                        warnings.append(stop_provider)
                        continue
                    candidates: dict[tuple[str, str], tuple[PhotoCandidate, float]] = {}
                    for priority, search in enumerate(plan.variants):
                        try:
                            found, hit = await self.cache.search(search)
                            report.provider_cache_hits += int(hit)
                            report.provider_requests += int(not hit)
                        except PhotoError as exc:
                            report.provider_requests += int(exc.request_made)
                            warnings.append(exc.code)
                            stop_provider = exc.code
                            break
                        for candidate in found[:24] if hinted else found:
                            identity = (candidate.provider, candidate.provider_asset_id)
                            if not self.policy.candidate_allowed(candidate, plan.sensitive):
                                continue
                            known = next(
                                (
                                    a
                                    for a in library
                                    if a.id == aliases.get(identity)
                                    or (a.provider, a.provider_asset_id) == identity
                                ),
                                None,
                            )
                            if known is not None:
                                if (
                                    self.eligible(known, plan.sensitive)
                                    and known not in assets
                                    and preserved_counts[known.id] < reuse
                                ):
                                    assets.append(known)
                                continue
                            if identity in aliases:
                                continue
                            score = self.ranker.score(candidate, search, 0 if hinted else priority)
                            if identity not in candidates or score > candidates[identity][1]:
                                candidates[identity] = candidate, score
                        if not hinted and len(candidates) + len(assets) >= len(submission_ids):
                            break
                    ranked = sorted(
                        candidates.values(),
                        key=lambda item: (-item[1], item[0].provider, item[0].provider_asset_id),
                    )
                    budget = max(0, target - len(assets)) + self.settings.photo_download_spare
                    attempts = 0
                    while ranked and len(assets) < target and attempts < budget:
                        creator_counts = Counter(a.creator_name for a in assets)
                        batch_size = min(
                            self.settings.photo_download_concurrency,
                            target - len(assets),
                            budget - attempts,
                        )
                        batch = []
                        for _ in range(min(batch_size, len(ranked))):
                            ranked.sort(
                                key=lambda item: (
                                    creator_counts[item[0].creator_name],
                                    -item[1],
                                    item[0].provider,
                                    item[0].provider_asset_id,
                                )
                            )
                            picked = ranked.pop(0)
                            batch.append(picked)
                            creator_counts[picked[0].creator_name] += 1
                        results = await asyncio.gather(
                            *(self._import(c, category, plan.sensitive) for c, _ in batch)
                        )
                        attempts += len(batch)
                        for asset, created, error in results:
                            if error:
                                warnings.append(error)
                            if asset is not None:
                                if created:
                                    report.downloaded_assets += 1
                                    imported_ids.add(asset.id)
                                if all(a.id != asset.id for a in assets):
                                    assets.append(asset)
                                if all(a.id != asset.id for a in library):
                                    library.append(asset)
                    if len(assets) < len(submission_ids):
                        warnings.append("limited_unique_photos")
        except TimeoutError:
            for category in categories.values():
                category.warnings.append("planning_timeout")
        visual_scores: dict[UUID, dict[UUID, float]] = {}
        allowed_assets: dict[UUID, set[UUID]] = {}
        archive_sources: dict[UUID, dict[UUID, UUID]] = {}
        from dropgrid.photos.pool import PoolCandidate, archive_pool, materialize_archive, rank_pool

        if self.visual:
            communities = {s.id: s.community_id for s, _ in rows}
            for context, submission_ids in groups.items():
                category, hint, wanted = context
                for sid in submission_ids:
                    community_id = communities[sid]
                    warnings = categories[category].warnings
                    pool = [
                        PoolCandidate(
                            self._asset_candidate(a),
                            "library",
                            asset=a,
                            sha256=a.sha256,
                            perceptual_hash=a.perceptual_hash,
                        )
                        for a in available.get(context, [])
                        if a.provider != "vk_archive"
                    ]
                    try:
                        async with asyncio.timeout(self.policy.plan_timeout_seconds):
                            archives = await archive_pool(
                                self.archive, community_id, warnings, category
                            )
                            if not archives:
                                from dropgrid.photos.rotation import community_usage

                                async with self.sessions() as s:
                                    usage = await community_usage(s, community_id, utcnow())
                                if not usage:
                                    assets = [item.asset for item in pool if item.asset]
                                    scores = await self.visual.rank_assets(
                                        community_id, assets, category
                                    )
                                    allowed_assets[sid] = {a.id for a in assets}
                                    if scores and all(
                                        score.visual_score is not None for score in scores.values()
                                    ):
                                        visual_scores[sid] = {
                                            aid: score.final_score for aid, score in scores.items()
                                        }
                                    continue
                            pool += archives
                            mixed_ranked = await rank_pool(
                                self.visual, community_id, pool, category
                            )
                            # Only the highest-ranked viable archive is materialized.
                            # Lower archive candidates remain reference rows.
                            materialized = []
                            for item in mixed_ranked:
                                if item.reference:
                                    assert self.archive
                                    try:
                                        asset, created = await materialize_archive(
                                            self.archive, self, item, category
                                        )
                                    except (PhotoError, OSError) as exc:
                                        warnings.append(
                                            exc.code
                                            if isinstance(exc, PhotoError)
                                            else "archive_storage_unavailable"
                                        )
                                        continue
                                    if preserved_counts[asset.id] >= reuse:
                                        continue
                                    if all(a.id != asset.id for a in available[context]):
                                        available[context].append(asset)
                                    if created:
                                        imported_ids.add(asset.id)
                                        report.downloaded_assets += 1
                                    item.asset = asset
                                    archive_sources[sid] = {asset.id: item.reference.id}
                                    materialized.append(item)
                                    break
                                materialized.append(item)
                                # A better existing/Pixabay candidate requires no archive import.
                                if item.asset and preserved_counts[item.asset.id] < reuse:
                                    break
                            # Remaining assets are usable fallback, but an archive belonging
                            # to another community is never admitted via the category pool.
                            asset_items = [item for item in mixed_ranked if item.asset]
                            for item in materialized:
                                if item not in asset_items:
                                    asset_items.append(item)
                            allowed_assets[sid] = {
                                item.asset.id for item in asset_items if item.asset
                            }
                            visual_scores[sid] = {
                                item.asset.id: item.score.final_score
                                for item in asset_items
                                if item.asset and item.score
                            }
                    except TimeoutError:
                        warnings.append("visual_ranking_timeout")
                        allowed_assets[sid] = set()
        # Even without the optional embedder, rotation uses the same usage clock.
        # Re-read and lock lifecycle, categories, assignments and lease before changing references.
        async with self.sessions() as session, session.begin():
            campaign = await locked_campaign(session, campaign_id)
            lease = await session.get(PhotoPlanLease, campaign_id, with_for_update=True)
            if (
                campaign.status != CampaignStatus.ready
                or not lease
                or lease.token != token
                or lease.expires_at <= utcnow()
            ):
                raise ConflictError("Campaign changed while photos were being selected")
            current = list(
                (
                    await session.scalars(
                        select(Submission)
                        .where(Submission.campaign_id == campaign_id)
                        .order_by(Submission.id)
                        .with_for_update()
                    )
                ).all()
            )
            originals = {s.id: s.media_asset_id for s, _ in rows}
            if (
                any(s.media_asset_id != originals.get(s.id) for s in current)
                or len(current) != total
            ):
                raise ConflictError("Campaign assignments changed during planning")
            current_categories = {
                community_id: category
                for community_id, category in (
                    await session.execute(
                        select(GridCommunity.community_id, GridCommunity.category).where(
                            GridCommunity.grid_id == campaign.grid_id
                        )
                    )
                ).all()
            }
            if any(
                normalize_category(current_categories.get(s.community_id))
                != normalize_category(category)
                for s, category in rows
            ):
                raise ConflictError("Grid categories changed during planning")
            current_hints = {
                r.community_id: r.content_hint
                for r in (
                    await session.scalars(
                        select(GridCommunity).where(GridCommunity.grid_id == campaign.grid_id)
                    )
                ).all()
            }
            current_desired = {
                p.community_id: p.desired_content
                for p in (
                    await session.scalars(
                        select(CommunityContentProfile).where(
                            CommunityContentProfile.community_id.in_(
                                [s.community_id for s, _ in rows]
                            )
                        )
                    )
                ).all()
            }
            if any(
                current_hints.get(s.community_id) != hints.get(s.community_id)
                or current_desired.get(s.community_id) != desired.get(s.community_id)
                for s, _ in rows
            ):
                raise ConflictError("Grid content hints changed during planning")
            counts: Counter[UUID] = Counter(
                s.media_asset_id
                for s in current
                if s.media_asset_id and not (data.force and s.status == SubmissionStatus.pending)
            )
            reuse = data.max_reuse_per_asset or self.settings.photo_max_reuse_per_asset
            assignments: dict[UUID, UUID] = {}
            for context, submission_ids in sorted(groups.items(), key=lambda item: str(item[0])):
                category, hint, wanted = context
                valid = []
                for asset in available.get(context, []):
                    fresh = await session.get(MediaAsset, asset.id)
                    if fresh and self.eligible(fresh, self.builder.build(category).sensitive):
                        valid.append(fresh)
                eligible_ids = {s.id for s in current if s.status == SubmissionStatus.pending}
                if self.visual:
                    from dropgrid.db.models import CommunityReferencePhoto
                    from dropgrid.photos.references import eligible_for_archive_reuse
                    from dropgrid.photos.rotation import community_usage, recently_used

                    for sid in submission_ids:
                        community_id = next(s.community_id for s in current if s.id == sid)
                        usage = await community_usage(session, community_id, utcnow())
                        for asset in valid:
                            if recently_used(
                                usage,
                                asset.provider or "library",
                                asset.provider_asset_id or str(asset.id),
                                asset.sha256,
                                asset.perceptual_hash,
                                utcnow(),
                                asset.id,
                            ):
                                allowed_assets.get(sid, set()).discard(asset.id)
                            ref_id = archive_sources.get(sid, {}).get(asset.id)
                            if ref_id:
                                profile = await session.get(
                                    CommunityContentProfile, community_id, with_for_update=True
                                )
                                ref = await session.get(CommunityReferencePhoto, ref_id)
                                if (
                                    not profile
                                    or not ref
                                    or not eligible_for_archive_reuse(
                                        ref.posted_at,
                                        utcnow(),
                                        profile.archive_reuse_min_age_days,
                                        profile.archive_reuse_enabled,
                                        ref.enabled,
                                        profile.archive_reuse_max_age_days,
                                    )
                                ):
                                    allowed_assets.get(sid, set()).discard(asset.id)
                assignments.update(
                    assign_assets(
                        [sid for sid in submission_ids if sid in eligible_ids],
                        valid,
                        counts,
                        reuse,
                        pending,
                        visual_scores,
                        allowed_assets if self.visual else None,
                    )
                )
            for submission in current:
                if submission.id in assignments:
                    report.newly_assigned += int(originals[submission.id] is None)
                    submission.media_asset_id = assignments[submission.id]
                elif data.force and submission.status == SubmissionStatus.pending:
                    submission.media_asset_id = None
            assigned_ids = {s.media_asset_id for s in current if s.media_asset_id}
            report.unassigned = sum(s.media_asset_id is None for s in current)
            report.unique_assets = len(assigned_ids)
            report.reused_existing_assets = len(assigned_ids - imported_ids)
            by_id = {s.id: s for s in current}
            for category, ids in all_groups.items():
                result = categories[category]
                result.assigned_count = sum(by_id[sid].media_asset_id is not None for sid in ids)
                result.unique_asset_count = len(
                    {by_id[sid].media_asset_id for sid in ids if by_id[sid].media_asset_id}
                )
                if result.assigned_count < len(ids):
                    result.warnings.append("insufficient_photos")
                result.warnings = sorted(set(result.warnings))
            report.categories = list(categories.values())
        logger.info(
            "photo.plan.partial" if report.unassigned else "photo.plan.completed",
            extra={
                "campaign_id": str(campaign_id),
                "counts": {"assigned": total - report.unassigned, "unassigned": report.unassigned},
            },
        )
        return report
