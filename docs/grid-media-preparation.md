# Real grids and lightweight media preparation

Import → batch resolve → campaign preparation → ensure recent references for
communities actually needed → optional already-indexed archive → plan media.
This stage never selects a VK post, runs the sender, or submits anything to VK.
Do not synchronously warm up or archive-index a whole 500-community grid.

## Import and resolution

`POST /api/v1/grids/import` accepts an optional `grid_id` for additive reimport.
It updates category/comment, preserves deliberate content_hint values, and keeps
`GridCommunity.source_reference` after canonical aliases change. It does not
remove absent members. Without grid_id, import creates a new grid as before.
Comments are operator notes; hints remain explicit. There is no mass promotion.

The typed `VKClient.resolve_communities` accepts 1–1000 normalized references and
uses batches of **25** in `groups.getById(group_ids=...)`.
The [official VK 5.199 schema](https://github.com/VKCOM/vk-api-schema/blob/master/groups/methods.json)
defines group_ids as IDs or screen names. We use a conservative application
limit rather than assuming the schema's largest batch works in every case.

Batch results correlate by id/screen_name, never array position. club/public
numeric aliases normalize to the same numeric identity. A missing, renamed or
ambiguous alias uses a bounded singleton read fallback. A mismatched numeric
singleton never associates a different group. Transient/protocol/rate-limit
batch failures do not fan out; auth/security failures stop the operation.

Resolution persists status, checked_at, and numeric error_code, with no raw
responses. States: resolved, not_found, deactivated, private_or_unavailable,
transient_error, unresolved. Operator is_active is not overwritten by resolution;
temporary failures do not deactivate communities. Private status reflects the
selected account's observed access, not a permanent universal property.
Canonical alias collisions keep the original local row/domain and resolved ID.

`POST /api/v1/grids/{id}/resolve` accepts account_id, offset, limit (1–25).
Process chunks sequentially for a large grid; each request has bounded work.
Use DBTokenProvider and an imported active Account, never the legacy env token.

## Readiness and queue

- `GET /api/v1/grids/{id}/readiness`: total, resolved, active_resolvable,
  unavailable/not_found/deactivated/private/transient/unresolved, comments/hints,
  visual references, indexed archive, archive opt-in, references_ready,
  media_context_ready and ready_to_create_campaign.
- `GET /api/v1/grids/{id}/communities/{cid}/media-context`: current category,
  comment/hint and readiness; validates compatible embedding serialization and
  local reference file presence. Grid summaries aggregate metadata efficiently;
  individual preparation rechecks files before skipping a sync.
- `POST /api/v1/grids/{id}/media-preparation`: account_id, optional community_ids
  (up to 1000), retry_failed=false. Returns 202 and enqueues, never scans inline.
- `POST /api/v1/campaigns/{id}/media-preparation`: same input, restricted to that
  campaign's already-prepared submissions. This does not create/send posts.
- `GET /api/v1/grids/{id}/media-preparation?page=1`: 25 jobs per page, safe result
  snapshots, state, attempts and fixed error codes.

Jobs are unique by grid/community/account. Re-enqueue is idempotent. Current ready
jobs remain ready; stale references or changed hints/category can requeue them.
Failed/transient jobs need an explicit retry_failed action. PostgreSQL claims
use FOR UPDATE SKIP LOCKED and six-minute leases. Expired running jobs can be
reclaimed; completion must own the lease token. No Redis/Celery is introduced.
A job has a 150-second client budget; reference sync itself is bounded to 100
posts and 120 seconds. Partial useful reference caches survive failure/retry.

Grid Detail shows a compact summary and expandable queue visibility/actions.
Readiness does not imply that every unavailable community can be used. The
current real grid still needs selective preparation of additional communities
before its usable subset is ready for media planning.

## Preparation policy and worker

`prepare_community_media_context(grid_id, community_id, account_id)` resolves
when needed and warms up **12** compatible recent references (default 180-day
window). At or above the target, it performs no VK sync. Existing larger sets
are not deleted. A smaller target does not overwrite profile.reference_target_count.

Archive discovery is never automatic. Archive is optional, even when no archive
is indexed; reuse only works when the content profile opts in. Media preparation
returns existing archive state without a discovery scan. Pixabay/library and
recent references remain usable with archive reuse disabled.

Configuration:

```ini
CAMPAIGN_REFERENCE_WARMUP_TARGET=12
CAMPAIGN_REFERENCE_RECENT_DAYS=180
MEDIA_PREPARATION_CONCURRENCY=2
VK_READ_CONCURRENCY=1
PHOTO_DOWNLOAD_CONCURRENCY=4
PHOTO_DOWNLOAD_TIMEOUT_SECONDS=10
```

Concurrency is per process: two preparation lanes, one VK HTTP read in flight
plus the existing per-account limiter, four shared CDN download slots, and one
CLIP CPU inference. Run one media-preparation process for these resource bounds;
leases still prevent duplicate claims if another process is accidentally started.

The explicit worker does not instantiate the publication monitor or sender:

```bash
cd backend
uv run --extra visual python -m dropgrid.photos.preparation_worker
```

It must use the API's database, media storage, model and APP_SECRET_KEY. For
Docker, the opt-in `media-preparation` compose profile shares API configuration
and media volume and forces writes off, an empty write allowlist and no env token:

```bash
INSTALL_VISUAL=true VISUAL_EMBEDDING_ENABLED=true \
  docker compose --profile media-preparation up -d media-preparation
```

Use the same compose project/environment as your API. Starting the worker is an
explicit operator action; this live test queued only four sample communities.

## Preview invariants and timings

Each category-only/community-aware/mixed lane has unique
(provider, provider_asset_id) identities. Existing hash/pHash dedup is unchanged.
The previous cats report duplicated 7094808 in the final chat formatting, not in
its saved preview JSON. A regression test injects duplicate ranking results and
checks all lanes.

`PhotoPreviewInput.diagnostics=true` returns timings_ms only in development.
Stages: query retrieval, downloads, normalization, candidate embedding, reference
loading, archive preparation, ranking; archive_identity_refresh measures fresh
wall.getById reads. Values include nested stages and parallel operation time;
**do not add them to infer total wall-clock latency**. No URLs, payloads or tokens
are recorded. Production returns no timings. UI displays available stage timings.

Stock imports now run within existing bounded import/download slots. Archive
preparation runs in chunks of at most three, with per-reference cache locks
instead of a global network lock. Each archive preparation is capped at 15s,
CDN downloads at 10s; three consecutive failures stop later chunks with an
explicit warning. Individual failures do not discard healthy candidates.
Sampling across the full configured age window and scoring weights are unchanged.

## Live validation: 2026-10-09

Main user grid: 325b7475-0c97-442e-aaee-125d70a73e50,
“Основная пользовательская сетка — 521 community”. Reimport committed locally:
522 source entries → 521 unique communities, 110 comments, one duplicate
sueta125odin. Existing deliberate hints elsewhere were preserved; the real grid
has zero automatically promoted hints.

Full resolution: **514 resolved, 7 not_found**, private/deactivated/transient/
unresolved zero. **28 groups.getById requests**: 21 batches + 7 singleton fallbacks;
plus one users.get validation. Elapsed **27.93s**. Not found: platinum193, kyp19,
muzrus38s, kontora_opg03, growlers_rus, vazsemerka07, n_m_z_o.

Comment coverage (unchanged): **10 usable**, **99 shorthand**, **1 unknown**.
Usable examples: девушка с машиной, дембель, еда в казане, БМВ Е60/Е38, W201.
Shorthand: Д-П, П-Д, П, Д, ДС (including lowercase); unknown: 124.
“Usable” means recognized deterministic concepts, not automatically enabled hints.

Four queued sample jobs completed ready. lujbit had 20 references; bmwe6060 and
superfannycat had 12 each and required zero VK reads. tractor_kirovets warmed
0 → 12 in **44.80s**, using **one wall.get**, with isolated download_failed warnings.
No archive discovery ran. Real-grid readiness became 4/514 usable media contexts;
no warmup of the other 510 communities was started.

Historical lujbit preview: **262.16s**. First instrumented repeat: **53.64s**;
archive preparation 53.37s, CDN downloads 42.32s, identity refresh reads 10.19s,
embedding 0.34s, ranking 0.20s. This identified sequential archive CDN work as
the bottleneck. That repeat made ten wall.getById reads and prepared six previously
uncached archive photos. Cache state differs from the historical run, so it is
not a controlled speedup ratio. The final parallel repeat is recorded below.

Local ignored artifacts: real-grid-resolution-2026-10-09.json,
grid-preparation-smoke-2026-10-09.json, preview-timings-serial-2026-10-09.json.
VK writes remain zero; opt-in used for the preview comparison is restored afterward.

Final parallel repeat: **13.24s**, archive preparation 12.91s, query retrieval
0.017s, normalization 0.143s, embedding 0.581s, reference loading 0.034s,
ranking 0.240s. Four wall.getById reads; lanes 8/8/12, unique identities, isolated
download_failed warnings. Aggregate download duration was 20.54s across parallel
operations. Previously successful images were cached, so the improvement combines
bounded parallelism and cache reuse; it is not a like-for-like cold benchmark.
Artifacts: preview-parallel-report-2026-10-09.json and
preview-timings-parallel-2026-10-09.json in ignored media storage.

Total live VK calls in this task: **44** (users.get 1, groups.getById 28,
wall.get 1, wall.getById 14). Zero VK writes, archive discovery calls, new archive
MediaAssets, or media usage rows. All four sample jobs are ready; the worker was
not started for the entire grid. Full quality gate: 523 backend tests, 49 frontend
tests, Ruff, mypy, typecheck/build and additive Alembic checks.
