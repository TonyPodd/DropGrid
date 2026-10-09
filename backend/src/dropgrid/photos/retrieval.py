"""Bounded multi-query retrieval with fair quotas and explicit provenance."""

from dataclasses import dataclass, field

from dropgrid.photos.cache import SearchCache
from dropgrid.photos.domain import (
    MAX_CANDIDATES_PER_QUERY,
    MAX_RETRIEVAL_QUERIES,
    PhotoCandidate,
    PhotoError,
    PhotoPolicy,
    PhotoQueryPlan,
    PhotoRanker,
    PhotoSearch,
)
from dropgrid.photos.progress import emit


@dataclass
class RetrievedPhoto:
    photo: PhotoCandidate
    queries: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    items: list[RetrievedPhoto] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requests: int = 0
    cache_hits: int = 0


async def retrieve_photos(
    cache: SearchCache,
    plan: PhotoQueryPlan,
    policy: PhotoPolicy,
    *,
    target: int | None = None,
    max_candidates: int | None = None,
) -> RetrievalResult:
    result = RetrievalResult()
    if plan.sensitive and not cache.provider.supports_sensitive_context:
        result.warnings.append("provider_context_restricted")
        return result
    by_identity: dict[tuple[str, str], RetrievedPhoto] = {}
    lanes: list[list[tuple[str, str]]] = []
    searches = plan.variants[:MAX_RETRIEVAL_QUERIES]
    for query_index, search in enumerate(searches):
        await emit(
            "pinterest_search"
            if getattr(cache.provider, "name", "") == "pinterest"
            else "pixabay_search",
            query_index,
            len(searches),
        )
        try:
            found, hit = await cache.search(search)
            result.requests += int(not hit)
            result.cache_hits += int(hit)
        except PhotoError as exc:
            result.requests += int(exc.request_made)
            result.warnings.append(exc.code)
            break
        valid = [c for c in found if policy.candidate_allowed(c, plan.sensitive)]
        valid.sort(key=lambda c: (-PhotoRanker().score(c, search), c.provider, c.provider_asset_id))
        lane = []
        for photo in valid[:MAX_CANDIDATES_PER_QUERY]:
            identity = (photo.provider, photo.provider_asset_id)
            item = by_identity.setdefault(identity, RetrievedPhoto(photo))
            if search.query not in item.queries:
                item.queries.append(search.query)
            if identity not in lane:
                lane.append(identity)
        lanes.append(lane)
        if target is not None and len(by_identity) >= target:
            break
    await emit(
        "pinterest_search"
        if getattr(cache.provider, "name", "") == "pinterest"
        else "pixabay_search",
        len(lanes),
        len(searches),
        raw_candidates=len(by_identity),
    )
    seen: set[tuple[str, str]] = set()
    # Interleave searches so a broad category cannot consume every import slot.
    for i in range(MAX_CANDIDATES_PER_QUERY):
        for lane in lanes:
            if i < len(lane) and lane[i] not in seen:
                seen.add(lane[i])
                result.items.append(by_identity[lane[i]])
    if max_candidates is not None:
        result.items = result.items[:max_candidates]
    return result


def best_search(photo: PhotoCandidate, variants: tuple[PhotoSearch, ...]) -> PhotoSearch:
    return max(variants, key=lambda search: PhotoRanker().score(photo, search))
