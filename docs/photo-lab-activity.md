# Photo Lab and global activity

Photo Lab: `/communities/{id}?grid={grid-id}`. Activity Center: `/activity`.
Reference study, archive and comparison are explicit user actions. A page load
only reads local state. All five provider toggles feed one source-neutral ranked
pool; source filters keep the global rank. Click a card for source/query/identity,
original date and the target's five closest CORE examples. The displayed style
percentage is cosine similarity scaled for readability, not a probability.
Raw scores and the previous independent lanes remain under detailed comparison.

## Durable progress

`GET /api/v1/activity` aggregates existing ReferenceSyncJob, MediaPreparationJob
and Submission send/monitor state without invoking those services. Actual
monitor leases count as active; scheduled future checks are history, not running
work. Labels, fixed safe messages, timestamps and counters are exposed; raw
vendor responses, credentials, submission errors/evidence are omitted.

Archive and preview share one new `photo_operation_jobs` table because neither
had a durable queue. `POST /communities/{id}/photo-jobs` accepts `kind` (`archive`
or `preview`) and the corresponding validated input. GET the same path with
`/latest?kind=preview|archive` to recover progress and result after reload.
The read-only reference-study worker consumes these jobs, with bounded two
claims, eight-minute fencing lease, and existing profile operation leases.
It forces VK writes off and an empty write allowlist and instantiates no sender
or monitor. API failures persist a fixed code, never exception text. Actual
query, materialization, ranking and archive seek/scan counters are persisted.
Global polling shows current/recent activity and completion notifications across
pages. Retry after failure is explicit.

## Other-community library

`CROSS_COMMUNITY_REUSE_ENABLED=false` by default. With explicit local opt-in and
the Photo Lab toggle, other active communities with compatible context are
considered. A recognized model takes precedence over a broad category; E60 does
not match E38, Accord does not match Civic. Target is excluded. At most 2,000
indexed rows from a bounded community context are examined, using compatible
stored embeddings, dimensions and recent-use hashes. Deduplication collapses
canonical VK photo identity across source namespaces. Top 48 metadata candidates
are shortlisted; at most 16 are materialized, then use the existing VisualRanker.
Provenance retains source community/post, VK photo identity and original date.
Both Pinterest and category-library candidates are publication-ineligible and
rejected by campaign import/eligibility. Enabling preview does not authorize
cross-community publication. Stats describe indexed **other** source communities
and the bounded inspected rows, not the entire 514-community grid.

## CORE/AUX

Exact/perceptual duplicate collapse precedes density scoring. Mutual three-nearest
neighbor components preserve multiple sizeable clusters. Components smaller than
max(3, 10% of unique references) are AUX; high-density members of sizeable clusters
are CORE, with a six-example minimum where possible. Small samples and fragmented
graphs have explicit density fallback reasons. Cluster size/reason is returned
with each reference. No car-object exclusion rules or single-centroid classifier.

Optional text CLIP is deferred: the matching
[text int8 encoder](https://huggingface.co/Xenova/clip-vit-base-patch32/tree/main/onnx)
adds 64.1 MB plus tokenizer assets/runtime and needs version/preprocessing
compatibility validation. Current CPU deployment remains vision-only. Density
clustering does not guarantee exterior-only results; the live Honda ranking
still admits parts below the leading exterior matches.

## Read-only live experiment, 2026-10-09

One Honda source (`opera001`) and one BMW E60 source (`publicbmwbm1`): 20 references
and embeddings each; 28/24 posts scanned respectively. No grid scan, photos/write
methods, campaign assignment or sender startup.

Honda target: 101 references, CORE/AUX 71/30 → 30/71 (29 recent compatible CORE used for ranking). Category source 20 compatible
embeddings, 17 candidates/shortlist, 16 materialized. Preview 14.1 seconds. Best
matches include 16 VK category, 11 Pixabay, five existing library. First result
is an Accord exterior (cosine .800); the first eight are exteriors, while parts remain at #9/#10 and below. Own
archive has zero indexed age-eligible candidates in this local context.

BMW target: 12 references, seven compatible CORE examples. Category source 20
compatible embeddings, 15 candidates/shortlist/materialized. Preview 18.4 seconds.
Best matches: 15 VK category and 12 Pixabay. First 14 are source car photographs;
a non-car source photo remains at #15. Best cosine .820. Pixabay's visually
similar cars are not necessarily the exact model. Own archive is empty.

For each target direct Pinterest attempted only normal bounded anonymous requests;
`pinterest_search_unavailable`, zero raw/valid/materialized/embedded Pins. Planned
queries are canonical model, model + car/aesthetic/street. Retrieval stops at the
first provider failure; do not report four successful searches or prove Pinterest
quality from this result. No login, evasion, alternate network, or Apify request.

Generated contact sheets in ignored `backend/media/reports/`:
`honda-all-sources.jpg`, `bmw-all-sources.jpg`. Results are local PhotoOperationJob
records; preview bytes remain local. Experimental flags are explicitly on in
ignored development env, committed defaults stay off. VK_WRITE_ENABLED=false,
write allowlist empty. Existing sender behavior unchanged.

Warm repeat after final clustering: Honda 12.1 s, BMW 12.1 s including
queue/polling; Pinterest 2.091/0.797 s, category-library 0.378/0.150 s. Cached
images/embeddings were reused. Pinterest raw/valid/page counters stayed zero.

The final Honda cutoff moved small five/six-image parts/interior clusters to AUX
while preserving the larger exterior clusters. One historical CORE row lies
outside the recent ranking window: persisted CORE/AUX is 30/71, ranking uses 29.
