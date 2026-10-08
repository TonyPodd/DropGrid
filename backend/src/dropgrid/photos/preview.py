"""Bounded retrieval/ranking preview; imports candidates but never assigns submissions."""

from uuid import UUID

from sqlalchemy import select

from dropgrid.db.models import Community, GridCommunity, MediaAsset
from dropgrid.photos.domain import PhotoError
from dropgrid.photos.planner import CampaignMediaPlanner
from dropgrid.photos.reference_schemas import PhotoPreviewInput, PhotoPreviewItem, PhotoPreviewRead
from dropgrid.photos.visual_library import VisualLibrary
from dropgrid.services.catalog import ConflictError, get_entity


async def photo_preview(
    planner: CampaignMediaPlanner,
    visual: VisualLibrary,
    community_id: UUID,
    data: PhotoPreviewInput,
) -> PhotoPreviewRead:
    async with planner.sessions() as session:
        community = await get_entity(session, Community, community_id)
        category = community.category
        if data.grid_id:
            relation = await session.get(GridCommunity, (data.grid_id, community_id))
            if relation is None:
                raise ConflictError("Community is not in supplied grid")
            category = relation.category
    plan = planner.builder.build(category)
    assets: list[MediaAsset] = []
    warnings = []
    search = plan.variants[0]
    if plan.sensitive and not planner.cache.provider.supports_sensitive_context:
        warnings.append("provider_context_restricted")
    else:
        try:
            candidates, _ = await planner.cache.search(search)
            valid = [c for c in candidates if planner.policy.candidate_allowed(c, plan.sensitive)]
            valid.sort(
                key=lambda c: (-planner.ranker.score(c, search), c.provider, c.provider_asset_id)
            )
            for candidate in valid[: data.candidate_limit]:
                asset: MediaAsset | None
                # Reuse existing import without downloading it again.
                async with planner.sessions() as session:
                    known = await session.scalar(
                        select(MediaAsset).where(
                            MediaAsset.provider == candidate.provider,
                            MediaAsset.provider_asset_id == candidate.provider_asset_id,
                        )
                    )
                if known and planner.eligible(known, plan.sensitive):
                    asset = known
                else:
                    asset, _, warning = await planner._import(
                        candidate, plan.category, plan.sensitive
                    )
                    if warning:
                        warnings.append(warning)
                if asset and all(a.id != asset.id for a in assets):
                    assets.append(asset)
        except PhotoError as e:
            warnings.append(e.code)
    scores = await visual.rank_assets(community_id, assets, category)
    items = [
        PhotoPreviewItem(
            media_asset_id=a.id,
            base_score=scores[a.id].base_score,
            visual_score=scores[a.id].visual_score,
            final_score=scores[a.id].final_score,
            best_similarity=scores[a.id].best_similarity,
            reference_count=scores[a.id].reference_count,
        )
        for a in assets
    ]
    _, reference_ids, _ = await visual.references(community_id)
    if not reference_ids:
        warnings.append("visual_references_unavailable")
    return PhotoPreviewRead(
        community_id=community_id,
        category=category,
        references=reference_ids[:20],
        category_only=sorted(items, key=lambda r: (-r.base_score, str(r.media_asset_id))),
        community_aware=sorted(items, key=lambda r: (-r.final_score, str(r.media_asset_id))),
        warnings=sorted(set(warnings)),
    )
