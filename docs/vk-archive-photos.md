# VK Archive Photo Provider v1

Recent style references and archive candidates have different retrieval windows.
The former still collect about 100 recent photo posts (profile target, maximum
300, at most 400 inspected posts). The latter index historical owner-published
photos by age, independently of the recent reference count.

## Explicit policy and bounded discovery

Archive reuse defaults to **false**. The profile defaults to an inclusive
180–540 day window, with `max_age > min_age` and maximum 3650 days. Discovery is
allowed while reuse is disabled; that does not authorize candidate selection.

`POST /api/v1/communities/{id}/archive/sync` accepts an optional Account UUID and
`max_pages` (default 20, hard maximum 50). It reads `wall.get filter=owner`, up to
100 items/page. A read-only exponential search followed by binary refinement
seeks both age boundaries before the sequential region scan, with a shared probe
cache and budget of 48 calls (5 posts/probe), tolerance 25 offsets and maximum
probe offset 10,000,000. Pinned/deleted/malformed dates are ignored. Unusable or
unstable chronology triggers a warned, bounded fallback. The scan overlaps its
approximate boundaries conservatively and stops at the max-age boundary, exhaustion, the page bound or
a 300-second timeout. A pinned old post does not stop chronology traversal.
The endpoint reports `seek_calls`, `seek_posts_inspected`, `start_offset`,
`end_offset`, scanned posts, pages, discovered/existing candidates,
termination flags and sanitized warnings. A reached scan bound means a partial
index, not a complete archive. It never scans another community or schedules jobs.

Discovery stores metadata only, with no MediaAsset creation, image downloads or
embeddings. CommunityReferencePhoto retains stable VK post/photo IDs and has
separate `is_style_reference`, `archive_discovered`, `enabled` and age eligibility
semantics. Existing rows remain style references; archive-only rows are excluded
from the style fingerprint. Dimensions let obviously unsuitable candidates be
filtered before image processing. Profile status reports discovered count,
currently eligible count and oldest/newest eligible dates.

## Lazy source-neutral candidates

VKArchivePhotoProvider reads only rows belonging to the requested Community with
an explicit enabled policy, the current time window and valid identity/dimensions.
Only a bounded shortlist (up to 28) is prepared for ranking. Four equal-width
age strata cover the configured window (default 180–270, 270–360, 360–450,
450–540 days). Each stratum uses a stable hash of community/photo identity and
UTC date; round-robin selection redistributes sparse-stratum capacity. Retrieval
fetches at most 28 metadata rows per stratum after DB eligibility filtering,
then prepares at most 28 images in total. No newest-only LIMIT determines the pool. Each image is handled
serially, without holding a DB transaction across HTTP or CPU work. Preparation
uses verified local normalized bytes and the compatible cached embedding. If
bytes are absent/corrupt, `wall.getById` refreshes the stable post/photo identity;
a saved CDN capability is never blindly retried. Refreshed dates are rechecked
against the age window. No token or Cookie/Authorization header reaches the CDN.
Existing HTTPS/DNS pinning, redirect, byte, decoded-pixel and timeout limits apply.

PoolCandidate wraps the generic PhotoCandidate metadata, an optional MediaAsset or
archive reference, a cached embedding, hashes and original publication timestamp.
Preview leaves archive candidates in reference storage. Only an archive selected
by the planner is materialized to MediaAsset. Exact/perceptual duplicates link an
eligible existing asset; MediaProviderImport keeps the archive community/post
provenance and stable `owner_id_photo_id` source identity. Archive MediaAssets do
not become a global library source: another community must have its own indexed,
explicitly permitted historical candidate. No cross-community archive retrieval
is implemented.

## Ranking and rotation

Pixabay retrieval remains category based. Mixed ranking combines prepared archive
candidates, Pixabay assets and the existing local library. Hash deduplication runs
across sources; provider append order does not determine the winner.

Before visual scoring, exclude references with the same VK photo identity, SHA256
or a perceptual distance at/below the existing duplicate threshold. Compare only
compatible vectors; average the nearest five remaining references, or fewer.
Zero remaining references means an unavailable visual component, never a perfect
self-match. The existing normalized 0.60 visual / 0.30 metadata / 0.10 quality
weights remain in effect. If any candidate lacks usable visual evidence, the pool
uses the existing metadata scale consistently rather than mixing score scales.

Archive age adds a named, bounded penalty of at most 0.03 on the normalized scale.
It tapers linearly from min age to approximately 365 days, then stays zero.
It never rewards increasingly old content; max-age eligibility remains mandatory.
Legacy-score fallback scales this small penalty with the base-score scale.
Archive metadata labels reflect the configured community/grid category. They
express source taxonomy, not inferred objects or OCR labels. The ordinary metadata
ranker operates on these explicit context labels and dimensions; visual similarity
remains the major signal. Both sources can win under the same scoring weights.

CommunityMediaUsage tracks source identity with optional asset/submission IDs,
SHA/perceptual hash, first/last use and count. The 180-day cooldown excludes recent
exact source, asset, SHA and near-duplicate uses in that community. Original VK
`posted_at` never inserts usage. Verified DropGrid suggestion receipts update the
ledger once; existing recorded DropGrid receipts are backfilled by migration.
Preview/planning do not create usage or increment send counts. Selection rechecks
policy/cooldown before assignment. Any future sender must recheck them at send time;
this task does not implement a sender or perform VK writes.

## API/UI

The Community page exposes min/max ages, explicit opt-in, archive status and
«Проиндексировать архив». This action saves the explicit profile and performs one
bounded index operation. Existing recent-reference synchronization remains separate.

Photo preview retains category-only and Pixabay community-aware arrays, and adds
`mixed_source`, `archive_age_strata` (counts for the prepared archive pool) and
`archive_shortlist` (identity and age of every prepared archive candidate).
The UI displays the distribution. Each ranked entry includes source/identity, local asset/reference ID,
base/visual/final/age-reuse scores and archive publication date/age. Archive entries
can have a null MediaAsset ID; thumbnails use the local reference content endpoint.
No capability URL, embedding vector or credential is exposed.

Use the optional CPU ONNX configuration described in
[Community Visual References](community-visual-references.md). Model downloads
remain explicit. Inspect the two preview orders and images; score changes are not
an automatic claim of improved quality.

## Live read-only smoke, 2026-10-09

Existing `lujbit` (VK group 223746894), original policy disabled, window 180–540 days.
One archive discovery read 20 pages / 1999 posts in 44.11 seconds, creating 627
unique metadata rows (6 repeated identities encountered). The 20-page bound was
reached before the max-age boundary: this is a partial index. With a temporary
local opt-in, 238 rows met window/dimension policy, dated 2026-01-02–2026-04-11.

One preview prepared 12 archive images and 12 embeddings in 15.83 seconds, with no
preview warnings. Peak Linux RSS was about 415 MiB. VK calls: 20 `wall.get` and
12 `wall.getById`, all on this community. No photo/upload/write API was invoked.
No archive MediaAsset or CommunityMediaUsage row was created. Original opt-in
was restored to false, write flag stayed false and write allowlist stayed empty.

Pixabay-only top five IDs: 3405257, 5387333, 3403963, 3552159, 1709944, scores
0.7298, 0.7166, 0.7120, 0.7083, 0.7061. Mixed top five photo identities:
-223746894_456247154, -223746894_456247072, -223746894_456247146,
-223746894_456247142, -223746894_456247145; scores 0.7649, 0.7616,
0.7568, 0.7516, 0.7509. Their ages were about 182–187 days and visual top-five
cosine means 0.8110, 0.8236, 0.7904, 0.7626, 0.7742. This verifies observed
ranking and lazy preparation, not automatic superiority of archive images.


## Age-stratified retrieval smoke, 2026-10-09

The follow-up used only lujbit and the encrypted DB account token. Date seeking
made 20 wall.get calls / 100 probe posts, then scanned offsets 1200–5000:
38 pages / 3800 posts, crossing max age without warnings or scan-bound termination.
This added 2277 unique metadata rows (2904 total); 1069 rows met enabled age and
dimension policy during the temporary opt-in. The discovered window reaches about
180.82–539.63 days, from 2025-04-17 through 2026-04-11. Existing rows were reused.

The prepared shortlist had 28 entries, seven per equal-width stratum:

- 180–270 days: 265.73, 261.44, 232.78, 252.32, 250.37, 268.62, 221.71.
- 270–360 days: 283.52, 314.82, 312.28, 290.45, 306.80, 307.72, 292.33.
- 360–450 days: 443.87, 430.72, 398.53, 371.30, 373.83, 364.28, 448.75.
- 450–540 days: 521.84, 463.50, 503.48, 521.85, 518.20, 531.21, 512.36.

Mixed top-five source/age/visual/final:

- -223746894_456244193: vk_archive, 373.83 days, 0.8595, 0.8256.
- -223746894_456243588: vk_archive, 463.50 days, 0.8318, 0.8001.
- -223746894_456245750: vk_archive, 292.33 days, 0.8639, 0.7949.
- -223746894_456246944: vk_archive, 232.78 days, 0.7942, 0.7851.
- -223746894_456245189: vk_archive, 314.82 days, 0.7415, 0.7834.

Scan including seeking took 83.32 seconds, versus the previous partial-index
44.11 seconds / 1999 posts. The new run inspected 3900 posts including probes and
skipped the first 1200 wall offsets. It covered the configured window; it is not
a directly comparable speed benchmark against the old partial run. Total wall.get
calls were 58 (20 seeks + 38 scans). One preview took 30.92 seconds, with 28 image
downloads and 28 embeddings; peak Linux RSS was 505.7 MiB. Preparation remains
serial and cache-backed. Higher scores alone do not establish better image quality.

No archive MediaAssets or CommunityMediaUsage rows were created. Community opt-in
was restored to false; VK_WRITE_ENABLED remained false and allowlist empty.
No VK writes, other communities or new providers were used.
