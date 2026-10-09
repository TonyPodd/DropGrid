# Experimental Pinterest retrieval

Pinterest is **Photo Lab preview only**. Default `PINTEREST_SEARCH_ENABLED=false`.
No Pin becomes a MediaAsset, campaign assignment, CommunityMediaUsage or send.
Every candidate explicitly has `publication_eligible=false`: a public Pin is
not evidence of publication rights. The production planner also rejects this
provider and any ineligible candidate before download/import.

The [official Pinterest v5 client endpoint catalogue](https://github.com/pinterest/pinterest-python-generated-api-client)
provides account Pins operations and a partner search endpoint; normal account
Pins endpoints do not provide the unrestricted global public keyword search
needed here. DropGrid uses a replaceable `PinterestSearchBackend` instead.
There is no Pinterest login, cookie storage, CAPTCHA/proxy/anti-bot code.

## Optional hosted backend

Configure only ignored `backend/.env` (or the API deployment environment):

```ini
PINTEREST_SEARCH_ENABLED=true
APIFY_API_TOKEN=<your token>
PINTEREST_APIFY_ACTOR=<actor-id or owner/actor>
VISUAL_EMBEDDING_ENABLED=true
VISUAL_MODEL_PATH=<existing versioned CLIP model path>
```

The [Apify REST API](https://docs.apify.com/api/v2) adapter starts one bounded
actor run per uncached query and reads its default dataset. Authorization uses
a Bearer header, never a query parameter. Runs have a 45-second actor timeout,
45-second wait limit and client deadline. There are no automatic run retries.
A nonterminal run or vendor failure returns a fixed safe unavailable code.

**Actor compatibility must be checked before live use.** Apify actors do not
share a universal Pinterest input/output schema. This adapter expects:

```json
{"searchQueries": ["honda accord"], "maxPins": 25}
```

Each dataset item must provide:

```json
{
  "id": "123456789",
  "url": "https://www.pinterest.com/pin/123456789/",
  "imageUrl": "https://i.pinimg.com/originals/example.jpg",
  "width": 1200,
  "height": 800,
  "title": "Honda Accord",
  "description": "Optional description",
  "alt": "Optional alt text"
}
```

`pinId`/`pinUrl` aliases are accepted. Other actor schemas require a small adapter
behind the protocol or a compatible hosted wrapper; configuring an arbitrary
actor is not proof of compatibility. No actor or credentials are hardcoded.

## Bounds and cache

Up to four queries, 25 metadata rows per query, identity dedup and a global cap
of 100. The common retrieval quota retains at most 24 rows per query. English
car concepts preserve Honda Accord, BMW E60/E38 and Mercedes W201. Unknown
categories retain the existing conservative query behavior.

Only the first 24 cheap-ranked/interleaved candidates are materialized. Search
metadata is cached for at least 24 hours, with an actor-specific namespace.
Normalized JPEG bytes and versioned embeddings live in a separate preview
cache/table, under `media/previews/pinterest/`. The same CORE reference ranking
and score weights apply; there is no Pinterest source bonus. Exact/near image
duplicates do not occupy multiple comparison cards.

Pin identity and HTTPS canonical Pin URL must agree. Images must use exactly
`i.pinimg.com`. Downloads use existing public-IP validation and DNS-pinned
transport, at most two redirects, 10-second deadline, 8 MiB input and 20 million
pixels; decoded images and dimensions are validated and metadata stripped.
No authentication or cookies are sent to the image CDN.

## Local comparison

Run migrations and start API/frontend with the same DB, Media storage and CLIP
configuration. Start the reference-study worker as documented in
[Photo Engine](photo-engine.md). Open the community detail page with its real
`?grid=<uuid>` context, study recent posts, then click **Сравнить подбор фото**.
The four lanes show metadata-ordered Pixabay/Pinterest and each source ranked
against CORE references, plus the existing mixed production pool. Cards show
rank, source, query, visual/final scores and up to five nearest CORE references.
Pinterest bytes are served by `GET /api/v1/photo-previews/{id}/content`.

No configured backend: the lane is visibly unavailable, without external
requests. Empty/unavailable results are not evidence of retrieval quality.
Review contact sheets visually before claiming a successful niche match.

## Honda live observation, 2026-10-09

Target `accordclubrus`, community UUID `e44cca54-331b-4e67-836e-ac7ff2466805`.
A read-only study scanned 171 posts and found 100 representative photo posts,
reusing 99 cached references and adding one. The DB retained 101 historical
style references: 71 CORE and 30 AUX. AUX included a document, diagnostic screen,
parts diagram and sky image, but density alone did not remove every dashboard
or electrical-part image. This is an unsupervised relative density diagnostic,
not a semantic exterior-car classifier.

Visual scoring was active with the current CLIP model. The English four-query
Pixabay retrieval produced seven unique cards, including motorcycles and a
sofa. Ranking promoted automotive imagery but retrieval quality still failed
the Honda Accord niche check. Do not describe this as a successful comparison.
The local contact sheets are ignored generated artifacts in `backend/media/reports/`.

Apify credentials/actor were absent: **zero live Pinterest pins retrieved or
embedded**. Fixture-backed validation is separate from live quality validation.
The second niche experiment is deferred until Honda Pinterest retrieval can
be configured and visually verified. No VK writes were performed.

## Direct public search (primary experiment)

`PINTEREST_DIRECT_ENABLED=false` by default. Explicitly enabling it selects the
native `PinterestDirectBackend` ahead of optional Apify, without an API key.
It uses a dedicated DNS-pinned anonymous client: homepage handshake, then
`GET /resource/BaseSearchResource/get/` with `source_url`, encoded options and
optional bookmark. Pin parsing accepts image-bearing public Pin rows only.
`search_page(query, limit, cursor)` exposes normalized images and continuation.
Search caps two pages/query, 25 pins/query, four queries and 100 deduped identities.
The CLIP shortlist is now bounded at 32, with normalized-byte/embedding cache.

Only cookies learned by the dedicated anonymous client during public warmup/
resource requests are echoed, including anonymous session cookies required by
the public flow. The jar and transport authorization set are cleared before/
after each search, never persisted. Imported account cookies and Authorization
headers are not sent. Redirects are host-restricted, bodies
bounded to 4 MiB, timeouts bounded and responses cached under `direct-v1`.
The normal stable User-Agent identifies DropGrid; there is no fingerprint
spoofing, proxy rotation, login, CAPTCHA handling or request retry loop.
An empty, withheld, malformed or blocked feed becomes
`pinterest_search_unavailable`, preserving other-provider fallback.

Protocol reference and attribution: [tamnd/pinterest-cli](https://github.com/tamnd/pinterest-cli),
Apache-2.0; see [notice](third-party/NOTICE.md) and retained license. The native
Python adapter does not shell out to the reference project's binary.

Current live follow-up (2026-10-09): both Honda Accord and BMW E60 comparisons
ran with the direct backend. Anonymous public search returned a safe
`pinterest_search_unavailable` warning and zero Pins for both; other sources
remained operational. No bypass was attempted. The earlier Apify/Honda-only
notes above are the historical baseline. See [current results and activity
workflow](photo-lab-activity.md).


## Protocol control and parity fix, 2026-10-10

Upstream control used an unmodified local build of `tamnd/pinterest-cli` at
`c6886bbff4e18b1f430134bea863779f9b26f2d1` on the same machine/network, with cache
and proxy environment disabled. `pin search "honda accord" --limit 10 -o json`
and `pin search "bmw e60" --limit 10 -o json` each exited 0 with 10 records.
No proxy, login, CAPTCHA work or network substitution was used.

DropGrid baseline `b172286`, retested in that same runtime, returned
`pinterest_search_unavailable` and zero Pins for both. This confirms an
implementation mismatch rather than establishing network withholding.
The fix mirrors only upstream behavior:

- resource URL includes `_` as current Unix milliseconds;
- `source_url` uses query escaping (spaces as `+`), fixed resource page size 25;
- resource Accept and `X-Pinterest-PWS-Handler` headers match upstream;
- public homepage Accept matches upstream;
- the dedicated anonymous jar's warmup/resource cookies are kept in memory for
  that search, instead of discarding every non-CSRF cookie;
- decoder uses `data` when the `results` array is empty, as upstream does.

DropGrid retains its stable identifying User-Agent, bounded redirects, DNS
pinning, HTTPS hosts, response/time/page bounds, and public safe fallback. No
browser fingerprint emulation, retry loop, ranking or sender change.
These parity differences were fixed together; the control does not attribute
the failure to one parameter alone.

Internal DEBUG diagnostics expose only `http_status`, normalized
`response_content_type`, fixed `resource_status`, `has_data`, and
`raw_result_count`. They never include body, URL, headers or cookies. Tests cover
403, HTML/login response, empty JSON feed and parser mismatch. Public errors
remain `pinterest_search_unavailable`.

Direct live retest: Honda 46 raw rows over two pages, 27 distinct valid image
Pins, 25 returned at the requested limit. BMW 50 raw rows over two pages, 16
valid/returned Pins. Both ended with HTTP 200 JSON, resource success and data
present; cookie jars and transport cookie authorization were cleared afterwards.
Search and image caches retain public metadata/normalized bytes, never cookies.

Existing Photo Lab comparison with unchanged ranking: Honda was ready in 71.7s,
71 Pinterest candidates retrieved, 32 materialized/embedded, 19 Pinterest
candidates in `best_matches`; its existing bounded query set was `honda accord`,
`honda accord car`, `honda accord aesthetic`, `honda accord street`. One safe
`image_dimensions_rejected` warning. BMW was ready in 48.8s, 41 retrieved, 31
materialized/embedded, 10 Pinterest candidates in `best_matches`, no warnings.
Both used the existing visual/category/Pixabay comparison path; VK writes stayed
disabled and the write allowlist stayed empty. No sender was run.
