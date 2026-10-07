# Architecture

## Components and dependencies

API — FastAPI/Pydantic v2; routers validate/serialize and delegate to services.
Services manage imports, campaign preparation and catalog CRUD using a passed
AsyncSession; request dependency opens a transaction and commits or rolls back.
Persistence — SQLAlchemy 2 async + asyncpg/PostgreSQL, explicit Alembic migrations.
No module-level engine/session: API lifespan, bot and worker own their engines and
close them. Pure domain enums/parser have no I/O dependencies.

Web — React/TypeScript/Vite, browser API URL through `VITE_BACKEND_URL`.
Lists have loading/error/empty states and pagination. Docker serves compiled SPA
through nginx with history fallback.

Telegram — aiogram 3 polling, a separate optional process. `/status` checks backend
health and its own PostgreSQL connection. Config uses pydantic-settings and
SecretStr for credentials. Runtime logging is JSON (timestamp/level/logger/message).
No third-party request or exception payload logging.

Async session lifetime follows [SQLAlchemy guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html):
one session per transaction/task, never shared between concurrent prepare calls.

## Entities

- Account: user-authorized VK identity, gender/status and nullable encrypted-token slot.
- Community: canonical unique domain, optional numeric VK group identity and category.
- Grid / GridCommunity: explicit many-to-many association; category also stored per
  grid so subsequent imports do not overwrite the first community's category.
- MediaAsset: storage reference, metadata and usage history. `tags` is PostgreSQL
  `text[]`: homogeneous strings, native array operators, no JSON schema ambiguity.
  Photo Engine adds provenance, normalized hashes/dimensions and provider-import
  aliases. Existing legacy rows remain valid through nullable metadata migration.
- Campaign: grid, VK track URL and optional parsed IDs, caption, lifecycle status,
  per-campaign publication_check_hours (72 default, positive constraint).
- Submission: unique campaign/community, nullable account/media references, status,
  timestamps, errors, attempt count and discovered post URL.

UUID PKs use application uuid4. DateTimes are timezone-aware UTC; created/updated
values are populated by ORM defaults (updates via ORM), not DB triggers. Foreign
keys preserve history by restricting deletion. Enum values are PostgreSQL enums.
Nonnegative counters and positive publication delay are DB constraints.

## Parsing and importing

Normalizer accepts http(s) vk.com/vk.ru, www hosts, short domains, @domains and
club/public numeric aliases, canonicalizes lowercase and numeric forms to clubN.
Rejects other hosts, credentials, query/fragment, nested paths and invalid domains.
It cannot resolve renamed domains or domain-vs-numeric aliases without VK API.

Parser ignores blank lines and numeric indices. Unindexed uppercase text or
Cyrillic headings set the current category. `# category` explicitly disambiguates
headings (lowercase ASCII short names otherwise mean communities). Invalid and
repeated references become line errors; first occurrence/category wins.

Preview never touches DB. Import is one transaction, creates a grid and valid
members, reuses canonical Community through PostgreSQL ON CONFLICT. Duplicate
references never create duplicate associations. Existing Community category stays
unchanged; the association carries the current import category. No valid rows → 422.

## Campaign prepare

Row lock serializes prepare and patch for the same campaign. Draft/ready allowed;
other lifecycle states rejected. Insert uses ON CONFLICT DO NOTHING on the
campaign/community unique constraint. Repeated preparation preserves attempt
history and assigned account/media; status becomes ready. Grid ID is immutable
through PATCH and edits are allowed only in draft. No start/sending action exists.

## Worker and future queue

Worker is a separate process so polling/shutdown and future jobs do not depend on
API request lifetime or number of API replicas. Today it only pings PostgreSQL and
waits on a stop event, handles SIGTERM/SIGINT and disposes the engine. Failed DB
heartbeats are sanitized and retried on the next interval.

Redis/Celery add deployment and scheduling complexity without current job needs.
Later a PostgreSQL job queue (e.g. SELECT FOR UPDATE SKIP LOCKED) or Redis adapter
can invoke the same services using its own sessions. Claims, retry policy,
transaction boundaries and authorization must be designed before actual sending.

## VK integration boundary

VKClient owns one pooled httpx.AsyncClient (or uses caller-owned injected client).
Fixed official API URL, automatic configured API version, typed/sanitized failures,
read-only bounded retry and a shared process-local per-account limiter. Helpers
parse audio, serialize typed attachments, build payloads and match audio without I/O.
Photo uploader has a separate clean upload HTTP session, ephemeral trusted URL,
bounded local bytes/path input and explicit write guards. Transport is injectable.

API lifespan owns client/provider; services receive them through simple DI.
TokenProvider is the only future secret-retrieval boundary. Currently a development
single-account env fallback is supported; encrypted DB field is never treated as
plaintext. Validation returns an auth failure result so invalid status can commit;
other errors roll back the transaction. Resolver handles canonical domain collisions
without merging grid/history. No schema migration was required.

Metrics fields are emitted in JSON logs using a fixed whitelist. Diagnostics target
one explicit account/community and require an additional exact allowlist for writes.
No integration calls from Campaign prepare or worker, no public sending endpoint.
Research findings and unresolved suggestion/OAuth semantics are in
[vk-integration.md](vk-integration.md). Future work is genuine encrypted storage,
OAuth, then an authorized sender with outcome reconciliation.

## Photo Engine v1

Prepared Campaign → GridCommunity category → PhotoQueryBuilder → existing library
→ persistent cached PhotoProvider search → metadata ranking → bounded downloads
→ normalize/deduplicate → MediaAsset → Submission.media_asset_id.

The Pixabay adapter is isolated behind typed candidates. Search cache, coalescing
leases and rate slots use PostgreSQL; no Redis. Dedicated pinned-DNS HTTP transport
checks trusted hosts and public IPs at every redirect/connection. Pillow exports
metadata-free JPEG; SHA/dHash and provider aliases avoid duplicate imports.
Local content-addressed storage is backed by a persistent Docker media volume.

Campaign leases serialize plans across replicas, without long HTTP transactions.
Final assignments revalidate lifecycle/category/reference state. Imports hold a
short transaction lock only during dedup/storage persistence. Planning does not
change actual-use history or Submission lifecycle. Defaults preserve assignments,
prefer unique images and limit per-campaign reuse to three. Partial results keep
valid work and report safe warning categories.

Photo Engine intentionally does not know how VK authorization works. No sender,
VK calls or production OAuth is added. See [photo-engine.md](photo-engine.md) for
policy, provenance/license considerations, concurrency, API and extension points.
