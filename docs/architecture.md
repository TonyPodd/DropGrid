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
  No photo downloads or provider queries yet.
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

## Future VK integration

`integrations/vk/client.py` holds a small VKClient Protocol, receipt and sanitized
error type. It contains no transport implementation. Future official-API adapter
will resolve communities/tracks and perform authorized actions, with least-privilege
OAuth, explicit rate limiting and encrypted secret retrieval. Domain/services will
receive adapters rather than import a specific network client. Research actual API
capabilities before extending the current illustrative submit contract.

## Future Photo Engine (not implemented)

```text
Community category → PhotoQueryBuilder → PhotoProvider → Candidate photos
→ ranking/deduplication → MediaAsset
```

PhotoQueryBuilder derives a query from category. A later PhotoProvider boundary
will return candidates with provenance/licensing. Ranking and deduplication choose
eligible assets and the persistence layer records selection/usage. No speculative
provider classes, Unsplash/Pexels clients, AI APIs or scrapers in this foundation.
