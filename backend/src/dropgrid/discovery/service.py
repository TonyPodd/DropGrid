"""Leased PostgreSQL discovery, bounded official reads, isolated image analysis."""

import asyncio
import json
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, SecretStr, model_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.api.schemas import Input
from dropgrid.db.models import Account, Community, Grid, GridCommunity, utcnow
from dropgrid.db.models import CommunityDiscoveryCandidate as Candidate
from dropgrid.db.models import CommunityDiscoveryEvidence as Evidence
from dropgrid.db.models import CommunityDiscoveryRun as Run
from dropgrid.discovery.queries import Seed, build_queries
from dropgrid.discovery.scoring import (
    activity,
    centroid,
    final_score,
    topic_match,
    visual_similarity,
)
from dropgrid.domain.enums import AccountStatus
from dropgrid.domain.grid_parser import normalize_vk_community_reference
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.errors import VKAuthenticationError, VKError
from dropgrid.integrations.vk.models import VKCommunity
from dropgrid.photos.domain import PhotoError, normalize_category
from dropgrid.photos.engine import PhotoEngine
from dropgrid.photos.images import LocalMediaStorage, normalize_image
from dropgrid.photos.references import VKReferencePolicy, representative_photo
from dropgrid.photos.regions import BY_KEY, grid_region, parse_region, region_evidence
from dropgrid.photos.visual import VisualEmbedding, serialize_embedding
from dropgrid.services.catalog import ConflictError, get_entity


class RunInput(Input):
    grid_id: UUID
    account_id: UUID
    scope_type: Literal["grid", "community", "category", "region"] = "grid"
    scope_value: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_scope(self) -> "RunInput":
        if self.scope_type != "grid" and not self.scope_value:
            raise ValueError("A scoped discovery run requires scope_value")
        return self


def run_read(row: Run) -> dict[str, Any]:
    return {
        "id": row.id,
        "grid_id": row.grid_id,
        "scope_type": row.scope_type,
        "scope_value": row.scope_value,
        "state": row.state,
        "stage": row.stage,
        "queries": row.queries,
        "query_cursor": row.query_cursor,
        "counters": row.counters,
        "error_code": row.error_code,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "updated_at": row.updated_at,
    }


def candidate_read(row: Candidate) -> dict[str, Any]:
    region = grid_region(None, row.suggested_region_key)
    return {
        "id": row.id,
        "grid_id": row.grid_id,
        "vk_group_id": row.vk_group_id,
        "domain": row.domain,
        "name": row.name,
        "url": f"https://vk.com/{row.domain}",
        "suggested_category": row.suggested_category,
        "suggested_region_key": row.suggested_region_key,
        "region": region.as_dict() if region else None,
        "found_by_queries": row.found_by_queries,
        "seed_community_ids": row.seed_community_ids,
        "topic_score": row.topic_score,
        "visual_score": row.visual_score,
        "region_score": row.region_score,
        "activity_score": row.activity_score,
        "graph_score": row.graph_score,
        "final_score": row.final_score,
        "confidence": row.reason.get("confidence", "needs_review"),
        "status": row.status,
        "human_status": row.human_status,
        "human_reviewed_at": row.human_reviewed_at,
        "human_reviewer_id": row.human_reviewer_id,
        "classification": row.classification,
        "analysis_state": row.analysis_state,
        "reason": row.reason,
        "previews": [
            f"/api/v1/discovery/candidates/{row.id}/photos/{i}"
            for i in range(min(len(row.previews), 6))
        ],
        "approved_at": row.approved_at,
        "approved_by": row.approved_by,
    }


class Discovery:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: VKClient,
        tokens: TokenProvider,
        engine: PhotoEngine,
    ) -> None:
        self.sessions, self.client, self.tokens, self.engine = sessions, client, tokens, engine
        self.settings = client.settings
        self.storage = LocalMediaStorage(engine.storage.root / "discovery")

    async def enqueue(self, data: RunInput) -> dict[str, Any]:
        async with self.sessions() as db, db.begin():
            await get_entity(db, Grid, data.grid_id)
            account = await get_entity(db, Account, data.account_id)
            if (
                account.status != AccountStatus.active
                or not account.token_configured
                or account.vk_user_id is None
            ):
                raise ConflictError("Validate an Account token before discovery")
            rows = (
                await db.execute(
                    select(Community, GridCommunity)
                    .join(GridCommunity, GridCommunity.community_id == Community.id)
                    .where(GridCommunity.grid_id == data.grid_id, Community.is_active.is_(True))
                    .order_by(Community.id)
                )
            ).all()
            seeds = []
            for community, relation in rows:
                region = grid_region(relation.category, relation.region_key)
                if data.scope_type == "community" and str(community.id) != data.scope_value:
                    continue
                if data.scope_type == "category" and normalize_category(
                    relation.category
                ) != normalize_category(data.scope_value):
                    continue
                if data.scope_type == "region" and (
                    not region or region.region_key != data.scope_value
                ):
                    continue
                if data.category and normalize_category(relation.category) != normalize_category(
                    data.category
                ):
                    continue
                seeds.append(
                    Seed(
                        community.id,
                        community.name or community.domain,
                        relation.category,
                        relation.content_hint or relation.comment,
                        relation.region_key,
                    )
                )
            if not seeds:
                raise ConflictError("No active seeds match this discovery scope")
            # A row lock serializes double-clicks on the same Grid without a new queue.
            await db.get(Grid, data.grid_id, with_for_update=True)
            active = await db.scalar(
                select(Run).where(Run.grid_id == data.grid_id, Run.state.in_(("queued", "running")))
            )
            if active:
                raise ConflictError("A discovery run is already active for this Grid")
            row = Run(
                grid_id=data.grid_id,
                account_id=data.account_id,
                scope_type=data.scope_type,
                scope_value=data.scope_value,
                queries=build_queries(seeds, self.settings.community_discovery_max_queries),
                counters={
                    "seed_communities": len(seeds),
                    "queries_done": 0,
                    "raw_candidates": 0,
                    "filtered": 0,
                    "already_known": 0,
                    "visually_analyzed": 0,
                    "recommended": 0,
                },
            )
            db.add(row)
            await db.flush()
            return run_read(row)

    async def tick(self) -> int:
        lease = uuid4()
        async with self.sessions() as db, db.begin():
            # One global discovery task, even if two consumers are accidentally started.
            if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(742193)")):
                return 0
            if await db.scalar(
                select(Run.id).where(Run.state == "running", Run.lease_until > utcnow()).limit(1)
            ):
                return 0
            row = await db.scalar(
                select(Run)
                .where(
                    Run.state.in_(("queued", "running")),
                    or_(Run.lease_until.is_(None), Run.lease_until <= utcnow()),
                )
                .order_by(Run.created_at, Run.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return 0
            row.state, row.lease_token, row.lease_until = (
                "running",
                lease,
                utcnow() + timedelta(minutes=5),
            )
            row.started_at = row.started_at or utcnow()
            id, account = row.id, row.account_id
        from dropgrid.integrations.vk.read_only import preparation_read_only

        boundary = preparation_read_only.set(True)
        try:
            async with asyncio.timeout(240):
                token = await self.tokens.get_token(account)
                async with self.sessions() as db:
                    row = await db.get(Run, id)
                    assert row
                    cursor, queries, stage = row.query_cursor, row.queries, row.stage
                if cursor == 0 and stage == "searching":
                    await self.client.get_current_user(access_token=token, account_id=account)
                if cursor < len(queries):
                    result = await self.client.search_communities(
                        str(queries[cursor]["query"]),
                        access_token=token,
                        account_id=account,
                        count=30,
                    )
                    await self.record_search(id, lease, queries[cursor], result.items)
                else:
                    await self.analyze_next(id, lease, token, account)
        except ConflictError:
            pass  # An expired lease may only be reclaimed by the new owner.
        except (VKError, PhotoError, OSError, TimeoutError) as exc:
            async with self.sessions() as db, db.begin():
                current = await db.get(Run, id, with_for_update=True)
                if current and current.lease_token == lease:
                    current.state, current.finished_at = "failed", utcnow()
                    current.error_code = (
                        "vk_auth_unavailable"
                        if isinstance(exc, VKAuthenticationError)
                        else "vk_read_" + type(exc).__name__
                        if isinstance(exc, VKError)
                        else "discovery_analysis_unavailable"
                    )
        finally:
            preparation_read_only.reset(boundary)
            async with self.sessions() as db, db.begin():
                current = await db.get(Run, id, with_for_update=True)
                if current and current.lease_token == lease:
                    current.lease_token = current.lease_until = None
        return 1

    async def record_search(
        self, id: UUID, lease: UUID, query: dict[str, Any], groups: list[VKCommunity]
    ) -> None:
        async with self.sessions() as db, db.begin():
            run = await db.get(Run, id, with_for_update=True)
            if (
                not run
                or run.lease_token != lease
                or not run.lease_until
                or run.lease_until <= utcnow()
            ):
                raise ConflictError("Discovery lease expired")
            counters = dict(run.counters)
            counters["raw_candidates"] += len(groups)
            for group in groups[:30]:
                domain = normalize_vk_community_reference(group.screen_name or f"club{group.id}")
                known = await db.scalar(
                    select(Community)
                    .where(or_(Community.vk_group_id == group.id, Community.domain == domain))
                    .limit(1)
                )
                in_grid = bool(known and await db.get(GridCommunity, (run.grid_id, known.id)))
                candidate = await db.scalar(
                    select(Candidate)
                    .where(
                        Candidate.grid_id == run.grid_id,
                        or_(Candidate.vk_group_id == group.id, Candidate.domain == domain),
                    )
                    .with_for_update()
                    .limit(1)
                )
                total = (
                    await db.scalar(
                        select(func.count()).select_from(Evidence).where(Evidence.run_id == run.id)
                    )
                    or 0
                )
                evidence = await db.get(Evidence, (run.id, candidate.id)) if candidate else None
                if evidence is None and total >= self.settings.community_discovery_max_candidates:
                    counters["bounded_out"] = counters.get("bounded_out", 0) + 1
                    continue
                if candidate:
                    counters["duplicates"] = counters.get("duplicates", 0) + 1
                else:
                    total = (
                        await db.scalar(
                            select(func.count())
                            .select_from(Evidence)
                            .where(Evidence.run_id == run.id)
                        )
                        or 0
                    )
                    if total >= self.settings.community_discovery_max_candidates:
                        counters["bounded_out"] = counters.get("bounded_out", 0) + 1
                        continue
                    candidate = Candidate(
                        grid_id=run.grid_id,
                        vk_group_id=group.id,
                        domain=domain,
                        name=group.name[:200],
                        suggested_category=query.get("category"),
                        suggested_region_key=query.get("region_key"),
                        topic_score=topic_match(
                            str(query["topic"]), group.name + " " + group.description
                        ),
                        classification="already_in_grid"
                        if in_grid
                        else "known_elsewhere"
                        if known
                        else "new",
                        status="duplicate"
                        if in_grid
                        else "unavailable"
                        if group.is_closed or group.deactivated or group.type == "event"
                        else "new",
                        analysis_state="ready"
                        if in_grid or group.is_closed or group.deactivated or group.type == "event"
                        else "pending",
                        reason={
                            "topic": query["topic"],
                            "target_region_key": query.get("region_key"),
                            "filter": "already_in_grid"
                            if in_grid
                            else "not_public"
                            if group.is_closed or group.deactivated
                            else "event"
                            if group.type == "event"
                            else None,
                        },
                        found_by_queries=[],
                        seed_community_ids=[],
                        previews=[],
                    )
                    db.add(candidate)
                    await db.flush()
                if candidate.status in {"new", "reviewing", "unavailable"} and not in_grid:
                    candidate.analysis_state = "pending"
                    candidate.status = "new"
                    candidate.reason = {
                        **candidate.reason,
                        "topic": query["topic"],
                        "target_region_key": query.get("region_key"),
                    }
                candidate.found_by_queries = sorted(
                    set(candidate.found_by_queries) | {str(query["query"])}
                )
                candidate.seed_community_ids = sorted(
                    set(candidate.seed_community_ids) | set(query["seed_ids"])
                )
                if candidate.status == "rejected":
                    counters["previously_rejected"] = counters.get("previously_rejected", 0) + 1
                if in_grid:
                    counters["already_known"] += 1
                evidence = await db.get(Evidence, (run.id, candidate.id))
                if evidence is None:
                    evidence = Evidence(
                        run_id=run.id, candidate_id=candidate.id, queries=[], seed_community_ids=[]
                    )
                    db.add(evidence)
                evidence.queries = sorted(set(evidence.queries) | {str(query["query"])})
                evidence.seed_community_ids = sorted(
                    set(evidence.seed_community_ids) | set(query["seed_ids"])
                )
            run.query_cursor += 1
            counters["queries_done"] = run.query_cursor
            run.counters = counters
            if run.query_cursor == len(run.queries):
                run.stage = "checking_activity"

    async def analyze_next(
        self, run_id: UUID, lease: UUID, token: SecretStr, account: UUID
    ) -> None:
        async with self.sessions() as db:
            run = await db.get(Run, run_id)
            assert run
            candidate = await db.scalar(
                select(Candidate)
                .join(Evidence, Evidence.candidate_id == Candidate.id)
                .where(
                    Evidence.run_id == run_id,
                    Candidate.analysis_state == "pending",
                    Candidate.status.in_(("new", "reviewing")),
                )
                .order_by(Candidate.topic_score.desc(), Candidate.id)
                .limit(1)
            )
        if candidate is None:
            await self.finish(run_id, lease)
            return
        async with self.sessions() as db:
            evidence = await db.get(Evidence, (run_id, candidate.id))
            assert evidence
            matched = [q for q in run.queries if q["query"] in evidence.queries]
            targets = {str(q["region_key"]) for q in matched if q.get("region_key")}
            reason = {
                "topic": matched[0]["topic"],
                "target_region_key": next(iter(targets)) if len(targets) == 1 else None,
                "matched_region_keys": sorted(targets),
            }
            query_count = len(evidence.queries)
            current_seed_ids = evidence.seed_community_ids
        # A canonical candidate can be rediscovered after its topic/region changed.
        # Previous visual evidence must not consume this run's budget or survive filtering.
        candidate.visual_score = None
        candidate.visual_centroid = None
        candidate.visual_model = None
        candidate.visual_dimensions = None
        candidate.previews = []
        candidate.activity_score = 0.0
        candidate.final_score = 0.0
        candidate.graph_score = 0.0
        try:
            group = await self.client.describe_community(
                candidate.vk_group_id, access_token=token, account_id=account
            )
            public_text = " ".join(
                filter(
                    None,
                    (
                        group.name,
                        group.description[:4000],
                        str((group.city or {}).get("title", "")),
                    ),
                )
            )
            observed = region_evidence(public_text)
            region_status = (
                "same_region"
                if observed and observed.region_key in targets
                else "mismatch"
                if observed and targets
                else "unverified"
                if targets
                else "not_required"
            )
            candidate.name = group.name[:200]
            candidate.domain = normalize_vk_community_reference(
                group.screen_name or f"club{group.id}"
            )
            topic = topic_match(str(reason["topic"]), public_text)
            candidate.topic_score = topic
            candidate.suggested_region_key = observed.region_key if observed else None
            candidate.region_score = (
                1.0 if region_status == "same_region" else 0.0 if targets else None
            )
            reason.update(
                region_status=region_status,
                region_evidence=observed.as_dict() if observed else None,
            )
            filter_code = (
                "not_public"
                if group.is_closed or group.deactivated
                else "event"
                if group.type == "event"
                else "region_mismatch"
                if region_status == "mismatch"
                else "unrelated_topic"
                if topic < 0.5
                else None
            )
            if filter_code:
                candidate.status = "unavailable"
                reason["filter"] = filter_code
            else:
                wall = await self.client.get_community_wall_history(
                    group.id, access_token=token, account_id=account, count=100
                )
                signals = activity(wall.items, utcnow())
                reason["activity"] = signals
                candidate.activity_score = signals["score"]
                photos = [
                    photo
                    for p in wall.items
                    if type(p.get("date")) is int
                    and utcnow().timestamp() - 90 * 86400 <= p["date"] <= utcnow().timestamp()
                    and (photo := representative_photo(p, group.id, VKReferencePolicy()))
                ]
                photos = list({(p.owner_id, p.photo_id): p for p in photos}.values())[:24]
                analyzed = run.counters.get("visually_analyzed", 0)
                vectors: list[VisualEmbedding] = []
                previews = []
                seen_photos: set[str] = set()
                if (
                    signals["state"] != "dead"
                    and analyzed < self.settings.community_discovery_visual_candidates
                    and self.engine.embedder
                ):
                    await self.progress(run_id, lease, "analyzing_photos")
                    for photo in photos:
                        if len(vectors) >= self.settings.community_discovery_photos_per_candidate:
                            break
                        try:
                            raw = await self.engine.reference_downloader.download(photo.url)
                            image = await asyncio.to_thread(
                                normalize_image, raw, VKReferencePolicy()
                            )
                            if image.sha256 in seen_photos:
                                continue
                            seen_photos.add(image.sha256)
                            vector = await self.engine.embedder.embed_image(image.data)
                            vectors.append(vector)
                            previews.append(await asyncio.to_thread(self.storage.write, image))
                        except (PhotoError, OSError, TimeoutError):
                            continue
                visual = centroid(vectors)
                if visual:
                    references: dict[str, list[VisualEmbedding]] = {}
                    async with self.sessions() as db:
                        ids = [UUID(s) for s in current_seed_ids[:20]]
                        seed_names = {
                            str(c.id): (c.name or c.domain, c.domain)
                            for c in (
                                await db.scalars(select(Community).where(Community.id.in_(ids)))
                            ).all()
                        }
                    for sid in ids:
                        core, _, _ = await self.engine.visual.references(sid)
                        references[str(sid)] = core
                    closest = visual_similarity(visual, references)
                    candidate.visual_score = closest[0][1] if closest else None
                    (
                        candidate.visual_centroid,
                        candidate.visual_model,
                        candidate.visual_dimensions,
                    ) = serialize_embedding(visual), visual.model, visual.dimensions
                    reason["closest_communities"] = [
                        {
                            "community_id": sid,
                            "name": seed_names[sid][0],
                            "url": "https://vk.com/" + seed_names[sid][1],
                            "similarity": round(score, 4),
                        }
                        for sid, score in closest
                        if sid in seed_names
                    ]
                    reason["photos_analyzed"] = len(vectors)
                else:
                    reason["visual_unavailable"] = (
                        "model_disabled"
                        if not self.engine.embedder
                        else "no_usable_photos_or_bound"
                    )
                candidate.previews = previews[:6]
                candidate.final_score, candidate.graph_score = final_score(
                    candidate.topic_score,
                    candidate.visual_score,
                    candidate.region_score,
                    candidate.activity_score,
                    query_count,
                )
                reason["confidence"] = (
                    "high"
                    if candidate.final_score >= 0.72
                    and candidate.activity_score > 0
                    and candidate.visual_score is not None
                    and len(vectors) >= 8
                    and (not targets or region_status == "same_region")
                    else "needs_review"
                )
                reason["score_version"] = "deterministic-v1"
        except VKAuthenticationError:
            raise
        except VKError as exc:
            candidate.status = "unavailable"
            reason["filter"] = "wall_or_community_unavailable"
            reason["vk_error_code"] = exc.code
        candidate.reason, candidate.analysis_state = reason, "ready"
        async with self.sessions() as db, db.begin():
            run = await db.get(Run, run_id, with_for_update=True)
            if (
                not run
                or run.lease_token != lease
                or not run.lease_until
                or run.lease_until <= utcnow()
            ):
                raise ConflictError("Discovery lease expired")
            persisted = await db.get(Candidate, candidate.id, with_for_update=True)
            assert persisted
            if persisted.status not in {"rejected", "approved"}:
                for field in (
                    "name",
                    "domain",
                    "topic_score",
                    "suggested_region_key",
                    "region_score",
                    "activity_score",
                    "visual_score",
                    "visual_centroid",
                    "visual_model",
                    "visual_dimensions",
                    "previews",
                    "final_score",
                    "graph_score",
                    "reason",
                    "status",
                    "analysis_state",
                ):
                    setattr(persisted, field, getattr(candidate, field))
            counts = dict(run.counters)
            counts["analyzed"] = counts.get("analyzed", 0) + 1
            counts["visually_analyzed"] = counts.get("visually_analyzed", 0) + int(
                bool(candidate.visual_centroid)
            )
            counts["filtered"] += int(candidate.status == "unavailable")
            run.counters, run.stage = counts, "checking_activity"
        from dropgrid.db.models import CommunityReviewBatch, CommunityReviewBatchItem

        async with self.sessions() as db:
            snapshots = (
                await db.scalars(
                    select(Evidence.analysis_snapshot)
                    .join(
                        CommunityReviewBatchItem,
                        (CommunityReviewBatchItem.run_id == Evidence.run_id)
                        & (CommunityReviewBatchItem.candidate_id == Evidence.candidate_id),
                    )
                    .join(
                        CommunityReviewBatch,
                        CommunityReviewBatch.id == CommunityReviewBatchItem.batch_id,
                    )
                    .where(CommunityReviewBatch.state == "open")
                )
            ).all()
        protected = frozenset(
            key
            for snapshot in snapshots
            if snapshot
            for key in snapshot.get("preview_cache_keys", [])
        )
        evicted = await asyncio.to_thread(self.trim_cache, protected)
        from sqlalchemy.dialects.postgresql import insert

        from dropgrid.db.models import DiscoveryCacheState

        async with self.sessions() as db, db.begin():
            stmt = insert(DiscoveryCacheState).values(
                id=1, evicted_count=evicted, cleaned_at=utcnow()
            )
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "evicted_count": DiscoveryCacheState.evicted_count + evicted,
                        "cleaned_at": utcnow(),
                    },
                )
            )

    async def progress(self, id: UUID, lease: UUID, stage: str) -> None:
        async with self.sessions() as db, db.begin():
            row = await db.get(Run, id, with_for_update=True)
            if row and row.lease_token == lease:
                row.stage = stage

    async def finish(self, id: UUID, lease: UUID) -> None:
        async with self.sessions() as db, db.begin():
            run = await db.get(Run, id, with_for_update=True)
            if not run or run.lease_token != lease:
                raise ConflictError("Discovery lease expired")
            rows = (
                await db.scalars(
                    select(Candidate)
                    .join(Evidence, Evidence.candidate_id == Candidate.id)
                    .where(Evidence.run_id == id)
                )
            ).all()
            run.counters = {
                **run.counters,
                "deduplicated": len(rows),
                "already_known_unique": sum(c.classification == "already_in_grid" for c in rows),
                "filtered": sum(c.status == "unavailable" for c in rows),
                "needs_review": sum(
                    c.status == "new" and c.reason.get("confidence") != "high" for c in rows
                ),
                "new": sum(c.classification == "new" and c.status == "new" for c in rows),
                "recommended": sum(
                    c.reason.get("confidence") == "high" and c.status == "new" for c in rows
                ),
                "region_mismatches": sum(c.reason.get("filter") == "region_mismatch" for c in rows),
                "active": sum(c.reason.get("activity", {}).get("state") == "active" for c in rows),
                "dead": sum(c.reason.get("activity", {}).get("state") == "dead" for c in rows),
                "multi_seed": sum(len(c.found_by_queries) > 1 for c in rows),
            }
            for candidate in rows:
                evidence = await db.get(Evidence, (id, candidate.id))
                if evidence and evidence.analysis_snapshot is None:
                    evidence.analysis_snapshot = json.loads(
                        json.dumps(
                            {
                                **candidate_read(candidate),
                                "preview_cache_keys": list(candidate.previews),
                            },
                            default=str,
                        )
                    )
            run.state, run.stage, run.finished_at = "ready", "ranking_candidates", utcnow()

    def trim_cache(self, protected: frozenset[str] = frozenset()) -> int:
        from dropgrid.discovery.cache import prune

        return prune(self.storage.root, protected)


async def approve(
    db: AsyncSession,
    candidate_id: UUID,
    *,
    category: str | None,
    region_key: str | None,
    content_hint: str | None,
    reviewer_id: UUID,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    row = await get_entity(db, Candidate, candidate_id)
    await db.get(Grid, row.grid_id, with_for_update=True)
    locked = await db.get(Candidate, candidate_id, with_for_update=True, populate_existing=True)
    assert locked
    row = locked
    if row.human_status != "approved":
        raise ConflictError("A real human approval is required before adding to Grid")
    from dropgrid.db.models import CommunityReviewBatchItem, CommunityReviewDecision

    human_label = await db.scalar(
        select(CommunityReviewDecision)
        .join(
            CommunityReviewBatchItem,
            (CommunityReviewBatchItem.batch_id == CommunityReviewDecision.batch_id)
            & (CommunityReviewBatchItem.position == CommunityReviewDecision.position),
        )
        .where(CommunityReviewBatchItem.candidate_id == row.id)
        .order_by(CommunityReviewDecision.reviewed_at.desc())
        .limit(1)
    )
    if not human_label or human_label.decision != "approved":
        raise ConflictError("Latest human decision must approve this candidate")
    if row.status not in {"new", "reviewing"} or row.analysis_state != "ready":
        raise ConflictError("Only an analyzed inbox candidate can be explicitly approved")
    if region_key and region_key not in BY_KEY:
        raise ConflictError("Unknown canonical region")
    parsed_category = parse_region(category)
    if (
        parsed_category
        and parsed_category.region_known
        and region_key
        and parsed_category.region_key != region_key
    ):
        raise ConflictError("Category and confirmed region disagree")
    if row.reason.get("region_status") == "mismatch":
        raise ConflictError("Candidate conflicts with the regional discovery scope")
    community = await db.scalar(
        select(Community)
        .where(or_(Community.vk_group_id == row.vk_group_id, Community.domain == row.domain))
        .limit(1)
    )
    if community is None:
        community = Community(
            vk_group_id=row.vk_group_id,
            domain=row.domain,
            name=row.name,
            resolution_status="resolved",
            resolution_checked_at=utcnow(),
        )
        db.add(community)
        await db.flush()
    if await db.get(GridCommunity, (row.grid_id, community.id)):
        raise ConflictError("Community is already in the target Grid")
    evidence = (
        await db.scalars(
            select(Evidence).where(Evidence.candidate_id == row.id).order_by(Evidence.run_id)
        )
    ).all()
    if run_id and not any(e.run_id == run_id for e in evidence):
        raise ConflictError("Discovery run does not contain this candidate")
    provenance = {
        "discovery_candidate_id": str(row.id),
        "discovery_run_ids": [str(e.run_id) for e in evidence],
        "approved_at": utcnow().isoformat(),
        "approved_by": str(reviewer_id),
        "seed_evidence": row.seed_community_ids,
        "queries": row.found_by_queries,
        "discovery_run_id": human_label.snapshot.get("run_id") or (str(run_id) if run_id else None),
        "add_context_run_id": str(run_id) if run_id else None,
        "human_reviewer": str(human_label.reviewer_id),
        "human_decision_at": human_label.reviewed_at.isoformat(),
        "human_review_batch_id": str(human_label.batch_id),
        "human_review_revision": human_label.revision,
        "added_at": utcnow().isoformat(),
        "suggested_category": human_label.snapshot["candidate"].get("suggested_category"),
        "final_category": category,
        "suggested_region": human_label.snapshot["candidate"].get("suggested_region_key"),
        "final_region": region_key,
    }
    context = grid_region(category, region_key)
    if context and row.suggested_region_key and context.region_key != row.suggested_region_key:
        raise ConflictError("Confirmed region conflicts with the observed community region")
    db.add(
        GridCommunity(
            grid_id=row.grid_id,
            community_id=community.id,
            category=category,
            region_key=region_key,
            region_codes=list(context.region_codes) if context else None,
            content_hint=content_hint,
            discovery_provenance=provenance,
        )
    )
    row.status, row.approved_at, row.approved_by, row.approved_community_id = (
        "approved",
        utcnow(),
        reviewer_id,
        community.id,
    )
    await db.flush()
    return candidate_read(row)
