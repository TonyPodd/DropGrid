# Region intelligence and Community Discovery V1

Photo Review records human photo decisions. Photo Lab investigates one photo-selection problem.
Discovery finds new communities and presents them for explicit human approval. These remain separate screens.

## Regional context

`GridCommunity.category` is retained verbatim. Nullable `region_key` and `region_codes` belong to the
Grid relationship, not the global Community. Migration backfills only explicit region labels; imports
and category edits use the same strict parser. Unknown/contradictory codes remain recognized but
unmapped and produce `unknown_region_code`. Bare numbers, car models and numeric domains do not
activate this parser. Definitions are a small, extensible table covering the production audit plus
24/124 and 54/154. Base codes follow МВД приказ 766, приложение 1; three-digit coding follows
[the Ministry's amendment](https://mvd.consultant.ru/files/1057057). Codes are geographic aliases,
not assertions that a particular series is currently issued. New codes require an explicit definition.

Regional ranking first applies an explainable source tier: same-region cross-community VK,
old own-community archive, other verified regional source, explicit external regional evidence,
unverified thematic fallback. It then uses the existing visual/learned photo scores within that tier.
Different known regions are excluded even with better CLIP similarity. Materialization and final
planning recheck context, age, cooldown, pHash/SHA duplication and reuse limits. Cross-source matching
also retains car-model intent. `REGIONAL_OWN_ARCHIVE_MIN_AGE_DAYS=365` does not change the global
archive minimum. Own archives still require the existing opt-in and maximum age.

Pinterest queries use place names, not bare region numbers. Pinterest/Pixabay/library geography is
unverified: metadata does not establish where a photograph was taken. Such alternatives remain
available for review and unverified automatic fallback carries attention. Preview reports per-target
source counts and selected regional provenance; campaign reports regional assignment totals/source
mix. Old review snapshots and decisions are never rewritten; regional display is derived on GET.

## Read-only discovery

Open `/discovery`, select an already validated DB-backed Account and a Grid. Choose whole Grid,
Community, category, or region with optional category. Normalized queries derive from bounded
category/model/region clusters, with at most 12 queries by default, never one query per Grid row.
Evidence retains actual queries and seed IDs. Seed memberships are contextual provenance; repeated
execution of the same query is not independent evidence. `graph_score` counts distinct matching
queries, not repeated runs.

Official calls: `users.get`, `groups.search`, `groups.getById`, `wall.get(filter=owner)`. The existing
[official VK schema](https://github.com/VKCOM/vk-api-schema) supplies the response contract. Tokens
come from `DBTokenProvider`. Existing rate limiter and bounded read retry remain; writes are
explicitly blocked in the discovery task context as well as deployment settings. No scraping,
private API, joins, photo uploads, posting, or sender operations.

Each run admits at most 30 unique candidates including existing rows rediscovered in later runs.
Canonical `(Grid, VK group id)` and `(Grid, domain)` constraints deduplicate inbox identities; a
run/candidate evidence table preserves history. Private/event/known-grid, unrelated topic and
known-region mismatch are filtered before photo analysis. No member-count minimum is imposed.
Rejected and approved statuses survive searches; only explicit restoration returns a rejection to
New. Activity reads at most 100 posts once per admitted candidate: 30/90-day counts are sampled
lower bounds. `active/weak/dead` describes the most recent sampled post, not predicted acceptance.

Only up to 20 prequalified candidates get visual analysis. Each downloads sequentially, attempting
at most 24 recent public photo posts to obtain 8 usable normalized photos by default. Duplicate
image bytes are excluded. Existing CLIP vision embeddings produce a centroid compared with seed
CORE references. No CLIP training. Fewer than eight usable photos or missing regional evidence
cannot produce High confidence. Scores expose topic/visual/region/activity/distinct-query components;
weights are deterministic V1 heuristics, not learned probabilities. Human precision@top is unknown.

Discovery image cache is isolated under `media/discovery`, limited to 300 MiB / 30 days. Candidate
vectors and explainable evidence are PostgreSQL data. Images are not automatically inserted into
`CommunityReferencePhoto`. Approved communities can use normal reference warmup afterward.

## Durability and approval

Runs persist query cursor, stages, counters and failures in PostgreSQL. One global advisory claim
and a five-minute lease serialize discovery consumers. Each bounded step has a 240-second timeout;
a process interrupted before commit repeats only reads after lease expiry. Browser reload and API
restart cannot lose a queued run. The existing reference-study worker executes discovery one task
at a time; no Redis/Celery/new high-memory service. Activity Center exposes Searching communities,
Checking activity, Analyzing photos and Ranking candidates. API stores no Account credentials in
run/candidate responses.

Candidate Inbox filters category/region/status/minimum score and shows safe VK links, score components,
queries, sampled activity, up to six thumbnails and closest current communities. Add opens a confirmation
form for target Grid/category/region/content hint. Only confirmation creates/reuses Community and
GridCommunity, with reviewer, timestamps, candidate/run IDs and seed/query provenance. No campaign
or Submission is created. `Не подходит` persists rejection; Rejected filter offers explicit restore.

`COMMUNITY_DISCOVERY_SCHEDULE_ENABLED=false` is reserved configuration, not an implemented scheduler.
There is no autonomous recurring run, auto-add, campaign sender or discovered-community sending.
Production Compose pins writes off, empty allowlist, one read/download at a time and existing CPU limits.

## Verification

Use only an isolated migrated PostgreSQL for tests:

```sh
cd backend
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
pytest
ruff check src tests alembic
mypy
DATABASE_URL="$TEST_DATABASE_URL" alembic check
cd ../frontend
npm run test
npm run typecheck
npm run build
cd ..
pre-commit run --all-files
```

Live pilots write only discovery metadata/cache. Regional diagnostics rank cached/indexed sources
without preparing or modifying the real campaign. The deployment retains database/media volumes,
Accounts, APP_SECRET_KEY, existing review data, Caddy/TLS and backups. Before/after fingerprints of
real review snapshots, decisions, campaign assignments and media count verify preservation.
