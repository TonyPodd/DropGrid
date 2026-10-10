# Photo discovery policy

Photo Engine and Photo Lab share `DiscoveryPolicy` and `discover`:

- primary external discovery: Pinterest anonymous public search (direct enabled by default);
- fallback external discovery: Pixabay;
- internal: eligible MediaAsset library, prepared own VK archive, indexed VK category library.

Retrieval priority never adds a source bonus to final ranking. The existing exact/perceptual dedup,
visual ranking and community rotation/cooldown remain unchanged. Planning does not trigger archive
indexing. Category archive reuse remains under its existing preview-only policy.

## Bounds and fallback

`PHOTO_PRIMARY_MIN_CANDIDATES=16`, `PHOTO_PRIMARY_TARGET_CANDIDATES=32`.
Recognized model/entity queries remain specific (Honda Accord, BMW E60/E38, Mercedes W201).
Existing English variants are preferred for Pinterest. At most four search queries, stopping once
sufficient unique metadata is retrieved. At most 40 metadata candidates and 32 normalized previews.
Cheap metadata ranking/filtering precedes downloads. The post-dedup viable pool controls fallback;
when CLIP is configured, an embedding is required to count toward that threshold.

Photo Lab skips Pixabay when viable Pinterest volume reaches the minimum. Optional
`PHOTO_FALLBACK_DIVERSITY_CANDIDATES=0` (maximum 8) admits a small bounded diversity sample.
Pinterest unavailability is a warning and falls back to Pixabay plus internal sources.

## Publication boundary

Pinterest is a normal assignment source. The legacy `PINTEREST_PUBLICATION_ENABLED`
setting is retained for configuration compatibility and no longer gates assignment.
Only the selected viable Pin is imported through the normal dedup/provenance pipeline;
normalized bytes and embeddings are reused. The unrelated VK write guard is unchanged.

`MediaProviderImport` retains provider, Pin ID, Pin page URL, original image source URL,
retrieval query and `unverified-public-pin` provenance. Photo Lab uses source badges,
without preview-only or prominent rights banners.

## Caches, diagnostics, feedback

Search metadata cache retains results for at least 24 hours. Normalized bytes/embeddings are keyed
by stable Pin ID and reused across previews and planning; anonymous cookies are never persisted.
Provider contribution diagnostics report retrieved, deduplicated, materialized, embedded, top-10
and selected counts. Preview selected count is zero because it never assigns campaign media.

Feedback is metadata only: `CommunityPhotoFeedback` stores community, provider, source identity,
like/dislike and creation time. GET/PUT/DELETE `/api/v1/communities/{id}/photo-feedback` support
reading, replacing and removing ratings. Photo Lab exposes 👍/👎 in the candidate details.
Feedback does not affect production ranking or source preference.

The direct adapter supports bounded `related(pin_id, limit<=20)` using the upstream
RelatedPinFeedResource options and anonymous lifecycle; no production caller invokes it.
The bounded live experiment used one already-ranked seed each for Honda and BMW.
Both returned `pinterest_search_unavailable`; quality improvement could not be measured.
Expansion remains disabled and no bypass or retry was attempted. No expansion is executed during production retrieval.

## Live Pinterest-first comparison (2026-10-10)

### Honda

Pinterest: 35 unique metadata candidates, 32 materialized, 32 embedded.
Queries: honda accord, honda accord car.
Pixabay requests: 0; policy result: `not_needed`.
Internal: library 18, VK category 16, own archive 0.
Existing search/normalized/embedding caches were reused; no forced cache expiration.

| Rank | Source | Visual score |
|---|---|---|
| 1 | vk_category_archive | 0.800 |
| 2 | vk_category_archive | 0.787 |
| 3 | pinterest | 0.761 |
| 4 | pinterest | 0.734 |
| 5 | vk_category_archive | 0.764 |
| 6 | pinterest | 0.777 |
| 7 | pinterest | 0.762 |
| 8 | pinterest | 0.734 |
| 9 | pinterest | 0.677 |
| 10 | vk_category_archive | 0.763 |
| 11 | pinterest | 0.685 |
| 12 | pinterest | 0.789 |

Contact sheet is generated in ignored local media reports.

### BMW

Pinterest: 32 unique metadata candidates, 32 materialized, 32 embedded.
Queries: bmw e60, bmw e60 car, bmw e60 aesthetic.
Pixabay requests: 0; policy result: `not_needed`.
Internal: library 6, VK category 15, own archive 0.
Existing search/normalized/embedding caches were reused; no forced cache expiration.

| Rank | Source | Visual score |
|---|---|---|
| 1 | vk_category_archive | 0.820 |
| 2 | vk_category_archive | 0.778 |
| 3 | vk_category_archive | 0.757 |
| 4 | vk_category_archive | 0.778 |
| 5 | vk_category_archive | 0.715 |
| 6 | vk_category_archive | 0.804 |
| 7 | vk_category_archive | 0.786 |
| 8 | vk_category_archive | 0.789 |
| 9 | vk_category_archive | 0.739 |
| 10 | vk_category_archive | 0.773 |
| 11 | vk_category_archive | 0.722 |
| 12 | vk_category_archive | 0.764 |

Contact sheet is generated in ignored local media reports.
