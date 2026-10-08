# Community Visual References v1

References inform ranking; Pixabay still supplies new photos. Retrieval uses the
existing concise category query (including GridCommunity category when supplied).
`desired_content` and `avoid_content` provide small tag-overlap ranking adjustments;
`style_notes` is stored for humans, never sent as a search paragraph.

## Storage and collection

An optional one-to-one CommunityContentProfile keeps manual preferences, target
count (default 100, maximum 300), archive policy and last successful sync timestamp.
Existing communities need no profile. CommunityReferencePhoto stores published VK
post/photo identities, timestamp, validated CDN URL, separate local image, hashes
and optional embedding. Unique community/photo identity makes repeated syncs
idempotent. Recent style sync never converts references to MediaAsset. The separate opt-in
[archive provider](vk-archive-photos.md) lazily materializes selected historical candidates.

The collector uses the fresh encrypted Account credential through DBTokenProvider;
there is no environment-token fallback. One explicit sync reads only `wall.get`
with `owner_id=-vk_group_id`, `filter=owner`, pages up to 100, and at most 400
inspected posts / 300 references / 300 seconds. It stops when the photo target is
reached or the API is exhausted. Reposts, suggested/postponed posts and content
not published by the community are skipped. One deterministic primary photo per
post is selected; the largest policy-compliant size is downloaded. A short database
lease prevents overlapping syncs without holding a transaction during HTTP/CPU work.

A dedicated HTTPS downloader validates every redirect and DNS address, pins public
addresses for TLS, allows only VK CDN roots, forbids credential-bearing URLs and
Authorization/Cookie headers, and bounds redirects, bytes, time and decoded pixels.
Normalized images live under `MEDIA_STORAGE_DIR/references`. API thumbnails serve
local files, never expose CDN capability query strings. Backfill during sync uses
existing local bytes and does not redownload unchanged images.

## Optional CPU embeddings

The model is quantized CLIP ViT-B/32 vision from
[Xenova](https://huggingface.co/Xenova/clip-vit-base-patch32/blob/main/onnx/vision_model_quantized.onnx),
pinned to revision `d15189d7028b43f1d3e65039190477f6af591c2a` and verified SHA-256.
Weights are about 89 MB. ONNX Runtime and numpy are an optional `visual` dependency
group; no torch or GPU is required. Allow a few hundred MB of additional RAM and
CPU time proportional to uncached images. The first local development embedding
(including model initialization) took about 1.7 seconds; this is not a throughput
benchmark. CPU intra-op threads are limited to two. Embeddings stay local.

```bash
cd backend
uv sync --extra dev --extra visual
PYTHONPATH=src .venv/bin/python -m dropgrid.photos.visual_model
```

Set these in the ignored local environment, alongside existing credentials:

```ini
VISUAL_EMBEDDING_ENABLED=true
VISUAL_MODEL_PATH=models/clip-vision-int8.onnx
```

For Docker, build with `INSTALL_VISUAL=true`, place the verified model in the media
volume at `/app/media/models/clip-vision-int8.onnx`, and enable the runtime flag.
Default builds and runtime configuration remain free of ONNX requirements.
Downloading weights is explicit; backend startup never downloads a model.

VisualEmbedder is library-independent. FakeVisualEmbedder supplies deterministic
unit tests. Storage uses 512-dimensional L2-normalized little-endian float32 BYTEA
(2048 bytes), with explicit model/revision/preprocessing version and dimensions.
MediaAsset caches its vector once. Invalid vectors are rejected. Incompatible
models are ignored or re-embedded from local files, never compared silently.

## Ranking and preview

Visual score is the mean of the nearest `min(5, reference_count)` cosine
similarities, with best similarity and compatible reference count available.
The normalized final score uses named weights: 0.60 visual, 0.30 existing metadata,
0.10 quality/novelty. Cosine is mapped from [-1, 1] to [0, 1]; base score uses a
named scale of 15; desired/avoid tag adjustments and quality normalization have
named constants and tests. Hard campaign reuse limits remain authoritative.

No references, disabled embeddings or unavailable candidate embeddings retain the
existing deterministic metadata ranking for the whole candidate pool, avoiding
mixed score scales. Planning ranks within the category-retrieved candidate pool;
visual similarity does not expand Pixabay retrieval.

Community details at `/communities/{uuid}` expose the profile, paginated local
reference thumbnails, explicit sync and side-by-side preview. API:

- GET/PUT `/api/v1/communities/{id}/content-profile`
- POST `/api/v1/communities/{id}/references/sync`, optional `account_id`, `target_count`
- GET `/api/v1/communities/{id}/references?page=1&page_size=20`
- GET `/api/v1/communities/{id}/references/{reference_id}/content`
- POST `/api/v1/communities/{id}/photo-preview`, optional `grid_id`, `candidate_limit`

Preview imports at most 12 category candidates (default 8), caches their embeddings
and returns IDs and scores for category-only / community-aware ordering. It does
not assign Submissions or increment campaign usage. Ordering is evidence for visual
inspection, not a claim of improved quality.

## Archive preparation and future rotation

Archive reuse defaults to disabled, with an inclusive 180–540 day window. The
[VK Archive Photo Provider](vk-archive-photos.md) performs a separate bounded
age-based discovery and uses eligible same-community rows as lazy candidates.
Only selected candidates become MediaAsset. References remain available with
reuse disabled. CommunityMediaUsage now tracks actual DropGrid receipts and a
180-day cooldown. No sender or VK writes are implemented.

## Development live smoke (2026-10-08)

Existing imported community `lujbit` (VK group 223746894), category «ЦИТАТА»:
one bounded synchronization inspected 21 posts and created/downloaded/embedded
20 references with no warnings in 30.61 seconds. VK methods were one
`groups.getById` to resolve the local row, then `wall.get` with `filter=owner`.
Write flag stayed false and the write allowlist stayed empty.

The initial preview caught a CDN cookie regression: a Set-Cookie response caused
the next request to fail the transport credential guard. The downloader now
explicitly strips Cookie/Authorization and disables client auth on every request,
including redirects; a MockTransport regression covers consecutive requests and
redirects with Set-Cookie. Preview alone was repeated after the fix, with zero
additional VK calls. Eight candidates ranked without warnings in 11.36 seconds;
peak Linux process RSS was about 459 MiB.

Category-only top three Pixabay IDs: 3405257, 3403963, 2659786.
Community-aware top three: 3405257, 5387333, 3403963.
ID 5387333 moved from eighth to second (visual cosine top-5 mean 0.6030,
normalized final score 0.7166). The first candidate stayed first (visual 0.6381,
final 0.7298). These are observed ordering changes, not a quality claim.
