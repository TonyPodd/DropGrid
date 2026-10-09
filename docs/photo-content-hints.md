# Grid comments and intentional photo retrieval hints

`GridCommunity.comment` is an operator note (nullable TEXT, API limit 3000).
`GridCommunity.content_hint` is an intentional retrieval signal (nullable TEXT,
API limit 500). Both belong to a grid/community relation, not the global Community.
An additive migration preserves every existing relation with null values.

## Import and operator workflow

The original category/reference format remains supported. Trailing comments can
use whitespace, hyphen, en dash or em dash:

```text
# МУЗЫКА
vk.com/music.track124 — девушка с машиной
# ВОЕННЫЕ
vk.com/in_the_distance - дембель
# ДОБРОЕ УТРО
vk.com/clubrovenki    Д-П
```

Dotted/digit-prefixed aliases, numbered links (including `449vk.com/...`) and
original uppercase category headers with numbers/slashes are supported. URLs
remain restricted to the exact VK host allowlist, without credentials, queries,
fragments or extra path segments. Syntax acceptance does not establish that a
community exists: the existing groups.getById resolver validates identity/access.
Duplicates are reported, preserving the first relation/category/comment.

Import stores the trailing text in **comment only**. There is no automatic
comment-to-hint migration or shorthand interpretation: Д-П, П-Д, П, Д and ДС
stay notes. In the Grid page, open a row's comment/hint editor; explicitly copy
its comment to the hint field, edit if needed, then save. Clearing the hint returns
to category/desired retrieval. The photo-preview link carries the grid context.

`PATCH /api/v1/grids/{grid_id}/communities/{community_id}` accepts comment and
content_hint independently, supports null clearing, and rejects nonexistent
relations. Listing/detail responses expose both fields. A shared community in a
different grid retains its own independent fields.

## Retrieval versus ranking

Pixabay search is **retrieval**. CLIP similarity to recent community references is
**ranking**. Embeddings never go to Pixabay. This feature changes candidate
retrieval, retaining the existing visual/metadata/quality scoring weights and
archive self-match, age, cooldown, same-community policy and lazy import behavior.

PhotoQueryBuilder takes category, content_hint and optional profile desired_content.
The explicit vocabulary is in `photos/concepts.py`: known Russian inflections map
to concise English concepts; brand/model examples include BMW E60/E38 and W201.
Unknown tokens are ignored and wholly unknown hints fall back to the existing
category plan. No external translator or LLM is required. Extend the vocabulary
separately from the query assembly; a later generator can preserve the same bounds.

Hints/desired text are inspected only up to 500 characters and four recognized
concepts. A recognized signal yields at most four distinct queries: category,
hint concept variants, an independent desired signal or a concise combined query.
Hinted searches request 24 metadata hits each on page 1; no query exceeds 100
characters. Existing category-only plans retain up to three legacy search variants.
The shared preview retrieval keeps at most 24 candidates/query (96 total), merges
provider identities with query provenance and interleaves query lanes so the broad
category cannot consume every image import slot. Preview imports at most 12 stock
candidates (default eight); archive preparation retains its separate limit of 28.

Campaign preparation separates category/hint/desired contexts, uses cached queries,
limits hinted provider metadata to 24/query and imports at most four additional
stock candidates per hinted context plus the existing bounded download spare.
It checks for hint/desired changes before assigning media. This is media planning,
not sender implementation. Source-neutral stock/archive/library selection remains.
Provider-ID dedup precedes downloads; normalization/import and mixed-pool dedup use
SHA256/pHash and retain provenance instead of duplicating equivalent bytes.

avoid_content is exposed in preview and continues through the existing ranking
interface. It is not sent as negative Pixabay syntax. Existing lexical tag overlap
is not a semantic exclusion model; style notes are not appended to search queries.

## Diagnostics and scale

Preview exposes category, comment, content_hint, desired/avoid fields,
generated_queries and each candidate's retrieval_queries, source, base/visual/final
scores. Empty provenance on existing library/archive entries means no stock query
produced that entry in this request. `category_only` is metadata ranking of the
same retrieved stock pool, not an independent category-query-only baseline;
`community_aware` adds reference ranking and `mixed_source` combines all sources.
Human image inspection is still needed; higher scores do not establish quality.

Recommended lifecycle:

```text
grid import → resolve communities → campaign preparation
→ ensure references/archive for communities actually needed → plan media
```

Reference/archive sync remains explicit and bounded. Import does not resolve or
archive-index the approximately 500-community grid synchronously. No new photo
providers, cross-community archive reuse, sender or VK write calls are added.


## Real-grid and three-community verification, 2026-10-09

The supplied original export has 522 community lines, 521 unique references and
110 trailing comments. The only duplicate is sueta125odin (first entry retained).
No syntax-invalid community was lost. The normal application importer successfully
created 521 relations / 110 comments / zero automatic hints in an isolated test DB;
that verification transaction was rolled back. This is not a bulk VK existence
check: 428 references initially had no local numeric/resolved identity (including
25 absent records); two selected communities were resolved during the smoke.
Numeric aliases alone do not certify an active/resolved community.

A separate local demo grid was created through normal import/patch endpoints.
The original user grid was not replaced, and its hints were not auto-populated.

### lujbit / 223746894

Category: ЦИТАТА. Explicit hint: природа закат.
Queries: природа небо; sunset nature; nature sky sunset. Preview: 262.16 seconds.

Stock/base top five: pixabay:4750959 (0.7102), pixabay:7504605 (0.6792), pixabay:8023696 (0.6722), pixabay:5039388 (0.4996), pixabay:6911736 (0.4842).

Stock/community top five: pixabay:4750959 (0.7102), pixabay:7504605 (0.6792), pixabay:8023696 (0.6722), pixabay:7377942 (0.5134), pixabay:9280759 (0.5101).

Mixed top five: vk_archive:-223746894_456246944 (0.7852), vk_archive:-223746894_456244372 (0.7850), vk_archive:-223746894_456244075 (0.7848), vk_archive:-223746894_456246673 (0.7847), vk_archive:-223746894_456244205 (0.7784).

Warnings: download_failed. Existing 20 style references and the already indexed archive were reused. Temporary archive opt-in was restored to false.

### bmwe6060 / 203136798

Category: БМВ. Explicit hint: БМВ Е60.
Queries: бмв автомобиль; bmw e60; bmw car e60. Preview: 8.28 seconds.

Stock/base top five: pixabay:7274571 (0.7664), pixabay:7227552 (0.7590), pixabay:7227559 (0.7619), pixabay:7227570 (0.7436), pixabay:6935139 (0.5765).

Stock/community top five: pixabay:7274571 (0.7664), pixabay:7227559 (0.7619), pixabay:7227552 (0.7590), pixabay:7227570 (0.7436), pixabay:6935139 (0.5765).

Mixed top five: pixabay:7274571 (0.7664), pixabay:7227559 (0.7619), pixabay:7227552 (0.7590), pixabay:7227570 (0.7436), pixabay:6935139 (0.5765).

Warnings: none. Bounded recent-reference sync created and embedded 12 references; some other image downloads failed. No archive indexing was performed.

### superfannycat / 224171644

Category: КОТИКИ. Explicit hint: кот.
Queries: кошка; cat; cat garden. Preview: 9.66 seconds.

Stock/base top five: pixabay:4611189 (0.7876), pixabay:8451431 (0.7708), pixabay:8664948 (0.7813), pixabay:4541889 (0.5693), pixabay:5618328 (0.5642).

Stock/community top five: pixabay:4611189 (0.7876), pixabay:8664948 (0.7813), pixabay:8451431 (0.7708), pixabay:8618301 (0.5858), pixabay:7094808 (0.5742).

Mixed top five: pixabay:4611189 (0.7876), pixabay:8664948 (0.7813), pixabay:8451431 (0.7708), pixabay:8618301 (0.5858), pixabay:7094808 (0.5742).

Warnings: none. Bounded recent-reference sync created and embedded 12 references; some other image downloads failed. No archive indexing was performed.


The standalone local HTML preview embeds 45 candidate images with scores/query
provenance for human inspection. No score-only quality improvement is claimed.
Peak Linux RSS was 626.8 MiB. Completed smoke VK methods: users.get (1),
groups.getById (2), wall.get (2), wall.getById (23); all read-only and restricted
to the three selected communities. No archive MediaAssets or usage rows were
created; write flag remained false and allowlist empty. No campaign was sent.
