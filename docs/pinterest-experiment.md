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
