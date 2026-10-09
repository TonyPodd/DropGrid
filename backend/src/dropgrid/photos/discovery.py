"""Named retrieval roles shared by campaign planning and Photo Lab.

Provider priority controls network work, never the global ranking formula.
"""

from dataclasses import dataclass, field, replace
from uuid import UUID

from dropgrid.config import Settings
from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import PhotoError, PhotoPolicy, PhotoQueryPlan
from dropgrid.photos.pinterest_preview import PinterestPreview
from dropgrid.photos.pool import PoolCandidate
from dropgrid.photos.retrieval import RetrievalResult, retrieve_photos


@dataclass(frozen=True)
class DiscoveryPolicy:
    primary: str = "pinterest"
    fallback: str = "pixabay"
    internal: tuple[str, ...] = ("library", "vk_archive", "vk_category_archive")
    minimum: int = 16
    target: int = 32
    diversity: int = 0

    @classmethod
    def configured(cls, settings: Settings) -> "DiscoveryPolicy":
        return cls(
            minimum=settings.photo_primary_min_candidates,
            target=max(
                settings.photo_primary_min_candidates, settings.photo_primary_target_candidates
            ),
            diversity=settings.photo_fallback_diversity_candidates,
        )


@dataclass
class DiscoveryResult:
    pins: list[PoolCandidate] = field(default_factory=list)
    pins_retrieved: int = 0
    pins_materialized: int = 0
    pins_embedded: int = 0
    pin_queries: dict[str, list[str]] = field(default_factory=dict)
    fallback: RetrievalResult = field(default_factory=RetrievalResult)
    warnings: list[str] = field(default_factory=list)
    fallback_reason: str = "not_needed"
    primary_requests: int = 0
    primary_cache_hits: int = 0


async def discover(
    primary: PinterestPreview | None,
    fallback: SearchCache,
    plan: PhotoQueryPlan,
    community_id: UUID,
    settings: Settings,
    policy: PhotoPolicy,
    *,
    include_primary: bool = True,
    include_fallback: bool = True,
    for_campaign: bool = False,
) -> DiscoveryResult:
    roles = DiscoveryPolicy.configured(settings)
    result = DiscoveryResult()
    if primary and include_primary:
        primary.target_candidates = roles.target
        # Canonical model variants are already English; for broader categories
        # prefer the existing English mappings without inventing entity parameters.
        variants = tuple(v for v in plan.variants if v.lang == "en") + tuple(
            v for v in plan.variants if v.lang != "en"
        )
        primary_plan = replace(plan, variants=variants[:4])
        try:
            (
                result.pins,
                result.pins_retrieved,
                result.pin_queries,
                warnings,
            ) = await primary.compare(community_id, primary_plan)
            result.warnings.extend(warnings)
            result.pins_materialized = getattr(primary, "last_materialized", len(result.pins))
            result.pins_embedded = getattr(
                primary, "last_embedded", sum(i.embedding is not None for i in result.pins)
            )
        except PhotoError:
            result.warnings.append("pinterest_search_unavailable")
        retrieval = primary.last_retrieval
        if retrieval:
            result.primary_requests, result.primary_cache_hits = (
                retrieval.requests,
                retrieval.cache_hits,
            )
    viable = sum(
        (item.embedding is not None or not primary or not primary.visual.embedder)
        and (not for_campaign or policy.candidate_allowed(item.photo, plan.sensitive))
        for item in result.pins
    )
    if for_campaign and not settings.pinterest_publication_enabled:
        viable = 0
        result.fallback_reason = "primary_preview_only"
    elif viable < roles.minimum:
        result.fallback_reason = "primary_insufficient" if result.pins else "primary_unavailable"
    elif roles.diversity:
        result.fallback_reason = "diversity"
    if include_fallback and result.fallback_reason != "not_needed":
        sample = roles.diversity if result.fallback_reason == "diversity" else None
        result.fallback = await retrieve_photos(
            fallback, plan, policy, target=sample, max_candidates=sample
        )
        result.warnings.extend(result.fallback.warnings)
        if include_primary and primary and not result.pins:
            result.warnings.append("pinterest_fallback_used")
    return result
