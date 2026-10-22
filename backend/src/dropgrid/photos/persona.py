"""Pick one of a girl's own photos for each dating/selfie public of her account.

The public's wall style (CLIP similarity to its core reference photos) decides most:
a public of plain selfies gets a selfie, a mirror-photo public a mirror photo. Photo
tags matching the public's name/description/hints add a text signal. No VK calls.
"""

import re
from collections import Counter, defaultdict
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from dropgrid.db.models import (
    AccountPersonaPhoto,
    Campaign,
    Community,
    CommunityContentProfile,
    GridCommunity,
    MediaAsset,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import SubmissionStatus
from dropgrid.photos.concepts import retrieval_hint
from dropgrid.photos.domain import PhotoError, PhotoQueryPlan, normalize_category
from dropgrid.photos.pool import PoolCandidate
from dropgrid.photos.rotation import community_usage, recently_used
from dropgrid.photos.visual import (
    CommunityVisualRanker,
    RankedPhoto,
    VisualEmbedding,
    VisualScore,
)
from dropgrid.services.account_pools import distribute, persona_accounts, persona_pins, pool
from dropgrid.services.category_genders import UNPLACED, placements

if TYPE_CHECKING:
    from dropgrid.photos.planner import CampaignMediaPlanner

PERSONA_MODEL = "persona-v1"
VISUAL_WEIGHT = 0.70
TEXT_WEIGHT = 0.25
SPREAD_WEIGHT = 0.05
NO_REFERENCE_VISUAL = 0.5


def _stems(text: str) -> list[str]:
    # Crude Russian stemming: «зеркало» also matches «зеркале», «селфи» matches «селфак».
    return [w[: max(4, len(w) - 2)] for w in re.findall(r"\w{3,}", text.casefold())]


def text_signal(tags: list[str], text: str) -> float:
    """Share of the photo's tags whose every word appears in the public's own text."""
    words = set(re.findall(r"\w+", text.casefold()))
    if not tags or not words:
        return 0.0
    hits = 0
    for tag in tags:
        stems = _stems(tag)
        hits += bool(stems) and all(any(w.startswith(s) for w in words) for s in stems)
    return hits / len(tags)


def score_photo(visual: VisualScore, text: float, uses: int) -> tuple[float, float | None]:
    normalized = None if visual.top_k_mean is None else (visual.top_k_mean + 1) / 2
    final = (
        VISUAL_WEIGHT * (NO_REFERENCE_VISUAL if normalized is None else normalized)
        + TEXT_WEIGHT * text
        + SPREAD_WEIGHT / (1 + uses)
    )
    return final, normalized


async def plan_personas(
    planner: "CampaignMediaPlanner",
    campaign_id: UUID,
    submission_ids: list[UUID] | None,
    force: bool,
) -> tuple[int, int]:
    """Assign girl photos to pending dating/selfie submissions; returns (assigned, missing)."""
    from dropgrid.photos.planner import CampaignMediaPlanner
    from dropgrid.services.sending import usable_account

    async with planner.sessions() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        places = await placements(session, campaign.grid_id)
        rows = (
            (
                await session.execute(
                    select(Submission, Community)
                    .join(Community, Community.id == Submission.community_id)
                    .where(Submission.campaign_id == campaign_id)
                    .order_by(Community.domain, Submission.id)
                )
            )
            .tuples()
            .all()
        )
        wanted = {
            row.id
            for row, community in rows
            if places.get(community.id, UNPLACED).persona
            and row.status == SubmissionStatus.pending
            and (submission_ids is None or row.id in submission_ids)
            and (force or row.media_asset_id is None)
        }
        if not wanted:
            return 0, 0
        girls = await persona_accounts(session, campaign_id)
        accounts = [
            item
            for item in await pool(session, campaign_id, planner.settings)
            if usable_account(item[0])
        ]
        available = [
            (row, community)
            for row, community in rows
            if community.is_active
            and community.resolution_status == "resolved"
            and community.vk_group_id
        ]
        pins = await persona_pins(
            session, [row for row, _ in available if row.id not in wanted], girls
        )
        allocation, _ = distribute(available, accounts, places, girls, pins)
        photos: dict[UUID, list[MediaAsset]] = defaultdict(list)
        for account_id, asset in (
            await session.execute(
                select(AccountPersonaPhoto.account_id, MediaAsset)
                .join(MediaAsset, MediaAsset.id == AccountPersonaPhoto.media_asset_id)
                .where(
                    AccountPersonaPhoto.account_id.in_(girls),
                    MediaAsset.enabled.is_(True),
                )
                .order_by(AccountPersonaPhoto.created_at, MediaAsset.id)
            )
        ).tuples():
            if asset.sha256 and planner.storage.path(asset.storage_key).is_file():
                photos[account_id].append(asset)
        hints = {
            relation.community_id: relation
            for relation in await session.scalars(
                select(GridCommunity).where(GridCommunity.grid_id == campaign.grid_id)
            )
        }
        desired = {
            profile.community_id: profile.desired_content
            for profile in await session.scalars(
                select(CommunityContentProfile).where(
                    CommunityContentProfile.community_id.in_([c.id for _, c in rows])
                )
            )
        }
        uses: Counter[UUID] = Counter(
            row.media_asset_id for row, _ in rows if row.media_asset_id and row.id not in wanted
        )
        originals = {row.id: row.media_asset_id for row, _ in rows}
    chosen: dict[UUID, tuple[PhotoQueryPlan, list[PoolCandidate]]] = {}
    missing: list[UUID] = []
    vectors: dict[UUID, VisualEmbedding | None] = {}
    for row, community in available:
        if row.id not in wanted:
            continue
        account = allocation.get(row.id)
        if account is None or not photos[account]:
            missing.append(row.id)
            continue
        relation = hints.get(community.id)
        category = places.get(community.id, UNPLACED).category
        text = " ".join(
            part
            for part in (
                community.name,
                community.description,
                relation.comment if relation else None,
                relation.content_hint if relation else None,
            )
            if part
        )
        references: list[VisualEmbedding] = []
        if planner.visual and planner.visual.embedder:
            references, _, _ = await planner.visual.references(community.id)
        async with planner.sessions() as session:
            usage = await community_usage(session, community.id, utcnow())
        ranked: list[tuple[bool, PoolCandidate]] = []
        for asset in photos[account]:
            if asset.id not in vectors:
                try:
                    vectors[asset.id] = (
                        await planner.visual.asset_embedding(asset)
                        if planner.visual and references
                        else None
                    )
                except (PhotoError, OSError):
                    vectors[asset.id] = None
            vector = vectors[asset.id]
            visual = (
                CommunityVisualRanker().score(vector, references)
                if vector is not None
                else VisualScore(None, None, 0)
            )
            signal = text_signal(list(asset.tags), text)
            final, normalized = score_photo(visual, signal, uses[asset.id])
            item = PoolCandidate(
                photo=CampaignMediaPlanner._asset_candidate(asset),
                source="persona",
                asset=asset,
                embedding=vector,
                sha256=asset.sha256,
                perceptual_hash=asset.perceptual_hash,
                score=RankedPhoto(
                    base_score=final,
                    visual_score=visual.top_k_mean,
                    final_score=final,
                    best_similarity=visual.best_similarity,
                    reference_count=visual.reference_count,
                    metadata_score=signal,
                    quality_score=SPREAD_WEIGHT / (1 + uses[asset.id]),
                    normalized_visual_score=normalized,
                ),
            )
            recent = recently_used(
                usage,
                asset.provider or "persona",
                asset.provider_asset_id or str(asset.id),
                asset.sha256,
                asset.perceptual_hash,
                utcnow(),
                asset.id,
            )
            ranked.append((recent, item))
        # A photo this public already received recently goes last, never excluded:
        # one girl may only have a handful of photos.
        ranked.sort(
            key=lambda pair: (
                pair[0],
                -(pair[1].score.final_score if pair[1].score else 0),
                str(pair[1].asset.id if pair[1].asset else ""),
            )
        )
        items = [item for _, item in ranked]
        assert items[0].asset is not None
        uses[items[0].asset.id] += 1
        # Same context as library planning: review confirmation re-checks it.
        plan = planner.builder.build(
            normalize_category(category),
            retrieval_hint(
                relation.content_hint if relation else None,
                community.name,
                relation.comment if relation else None,
            ),
            desired.get(community.id),
        )
        chosen[row.id] = (plan, items)
    from dropgrid.photos.review import snapshot

    assigned = 0
    async with planner.sessions() as session, session.begin():
        for row_id, (plan, items) in chosen.items():
            fresh = await session.get(Submission, row_id, with_for_update=True)
            if (
                fresh is None
                or fresh.status != SubmissionStatus.pending
                or fresh.media_asset_id != originals[row_id]
            ):
                continue
            assert items[0].asset is not None
            fresh.media_asset_id = items[0].asset.id
            await snapshot(session, fresh, plan, items, items, PERSONA_MODEL, [])
            fresh.photo_attention = [
                flag for flag in fresh.photo_attention if flag != "small_candidate_pool"
            ]
            assigned += 1
        for row_id in missing:
            fresh = await session.get(Submission, row_id, with_for_update=True)
            if fresh is not None and fresh.status == SubmissionStatus.pending:
                if force:
                    fresh.media_asset_id = None
                if fresh.media_asset_id is None:
                    fresh.photo_attention = ["persona_photos_missing"]
    return assigned, len(missing)
