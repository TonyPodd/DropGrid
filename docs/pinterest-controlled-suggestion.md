# Pinterest quality sweep and controlled suggestion — 2026-10-10

Production-like discovery ran inside the actual local API container against its development DB and media volume. Weights, thresholds and fallback policy were unchanged; no automatic feedback was recorded. All sources were enabled, including available local library and archives. Own archives were empty/disabled under their existing reuse policy; category archive retrieval returned 16 for the girl/car context. No whole-grid sync or send ran.

## Quality sweep

### Community 1: superfannycat

- Category: КОТИКИ
- Hint: none; comment: none
- Queries: cat garden, cats
- Unique Pinterest metadata: 40; embedded: 32; compatible CORE references: 7
- Pixabay network requests: 0
- Internal retrieved: library=8, own archive=0, category archive=0
- Contact sheet (ignored local artifact): `backend/media/reports/cats-all-sources.jpg`

| Rank | Source | Visual score |
|---|---|---|
| 1 | library | 0.815 |
| 2 | library | 0.810 |
| 3 | library | 0.775 |
| 4 | pinterest | 0.698 |
| 5 | pinterest | 0.830 |
| 6 | pinterest | 0.823 |
| 7 | pinterest | 0.812 |
| 8 | library | 0.820 |

### Community 2: tractor_kirovets

- Category: ТРАКТОР
- Hint: none; comment: none
- Queries: tractor field, agricultural tractor
- Unique Pinterest metadata: 40; embedded: 32; compatible CORE references: 8
- Pixabay network requests: 0
- Internal retrieved: library=0, own archive=0, category archive=0
- Contact sheet (ignored local artifact): `backend/media/reports/tractor-all-sources.jpg`

| Rank | Source | Visual score |
|---|---|---|
| 1 | pinterest | 0.839 |
| 2 | pinterest | 0.836 |
| 3 | pinterest | 0.835 |
| 4 | pinterest | 0.843 |
| 5 | pinterest | 0.848 |
| 6 | pinterest | 0.853 |
| 7 | pinterest | 0.845 |
| 8 | pinterest | 0.850 |

### Community 3: lujbit

- Category: ЦИТАТА
- Hint: none; comment: none
- Queries: nature sky, calm landscape
- Unique Pinterest metadata: 40; embedded: 32; compatible CORE references: 9
- Pixabay network requests: 0
- Internal retrieved: library=14, own archive=0, category archive=0
- Contact sheet (ignored local artifact): `backend/media/reports/quotes-all-sources.jpg`

| Rank | Source | Visual score |
|---|---|---|
| 1 | library | 0.412 |
| 2 | library | 0.603 |
| 3 | library | 0.560 |
| 4 | library | 0.548 |
| 5 | pinterest | 0.614 |
| 6 | pinterest | 0.606 |
| 7 | pinterest | 0.569 |
| 8 | pinterest | 0.478 |

### Community 4: izh_sueta

- Category: МУЗЫКА
- Hint: none; comment: девушка  с машиной
- Queries: woman car, girl car
- Unique Pinterest metadata: 33; embedded: 32; compatible CORE references: 6
- Pixabay network requests: 0
- Internal retrieved: library=7, own archive=0, category archive=16
- Contact sheet (ignored local artifact): `backend/media/reports/girlcar-all-sources.jpg`

| Rank | Source | Visual score |
|---|---|---|
| 1 | vk_category_archive | 0.755 |
| 2 | vk_category_archive | 0.746 |
| 3 | vk_category_archive | 0.730 |
| 4 | vk_category_archive | 0.837 |
| 5 | vk_category_archive | 0.721 |
| 6 | vk_category_archive | 0.702 |
| 7 | vk_category_archive | 0.700 |
| 8 | vk_category_archive | 0.806 |

### Community 5: in_the_distance

- Category: ВОЕННЫЕ
- Hint: none; comment: дембель
- Queries: soldier, military
- Unique Pinterest metadata: 40; embedded: 32; compatible CORE references: 13
- Pixabay network requests: 0
- Internal retrieved: library=0, own archive=0, category archive=0
- Contact sheet (ignored local artifact): `backend/media/reports/military-all-sources.jpg`

| Rank | Source | Visual score |
|---|---|---|
| 1 | pinterest | 0.736 |
| 2 | pinterest | 0.615 |
| 3 | pinterest | 0.750 |
| 4 | pinterest | 0.678 |
| 5 | pinterest | 0.673 |
| 6 | pinterest | 0.730 |
| 7 | pinterest | 0.716 |
| 8 | pinterest | 0.677 |

### Community 6: bpan_vaz2114

- Category: ВАЗ 2114 2115
- Hint: none; comment: none
- Queries: vaz 2114, vaz 2114 car, vaz 2114 aesthetic, vaz 2114 street
- Unique Pinterest metadata: 28; embedded: 28; compatible CORE references: 18
- Pixabay network requests: 0
- Internal retrieved: library=0, own archive=0, category archive=0
- Contact sheet (ignored local artifact): `backend/media/reports/vaz-all-sources.jpg`

| Rank | Source | Visual score |
|---|---|---|
| 1 | pinterest | 0.807 |
| 2 | pinterest | 0.813 |
| 3 | pinterest | 0.764 |
| 4 | pinterest | 0.726 |
| 5 | pinterest | 0.714 |
| 6 | pinterest | 0.709 |
| 7 | pinterest | 0.703 |
| 8 | pinterest | 0.631 |

Visual review: cats, tractors and VAZ candidates were thematically recognizable. Girl/car top results came from the category archive. Quote/nature retrieval yielded landscapes, but existing library assets (including a helicopter) outranked them. Military results include a figurine collage and stylized images, so human review remains necessary. Scores are not a human quality verdict. No weights were adjusted from this sweep.

## Deterministic query corrections

The imported Music community stored “девушка с машиной” in its grid comment, not the dedicated hint column. Shared preview/planner retrieval now uses recognized comment concepts only when no explicit hint or model name is available. Unknown shorthand still does not enter search. Planner revalidation includes the raw comment to prevent assigning from stale context. VAZ model categories now retain their specific English model identity.

## Manual feedback review

Append `review=<comma-separated-community-UUIDs>` and the existing `grid=<UUID>` to Photo Lab. The bounded review list preserves grid context, offers Next community, and counts communities with persisted manual feedback. Merely visiting does not count as reviewing; feedback still does not train ranking.

## Controlled planner

Tiny grid `e6ffdfc3-0a5c-4f83-a302-fa9962c77485`; campaign `0330e721-dc92-404c-91de-f68378838d7a`; one submission `503b35b2-b87c-493a-bfbe-f7c7dd2a3ac4`; community 242100737. Context Honda Accord controlled. Default publication stays false; ignored operator-local opt-in is true.

Real planner selected Pinterest Pin `4596064254946750336` and MediaAsset `50dc6783-985a-40dc-98de-45b329bd0478`. Initial planning used two search-cache hits and zero provider requests, imported one asset, and selected one Pinterest candidate. Normalized bytes and embedding match the existing preview cache. Sanity pass performed zero downloads/embeddings; JPEG 2160×1620, enabled, matching SHA and present in actual runtime storage. Provenance includes the Pin page, image source URL and query `honda accord car`; license remains unverified-public-pin. Exact JPEG was rendered before sending.

## Upload investigation and send

1. Sender attempt: get_upload_server succeeded (333 ms), multipart HTTP 200 failed protocol validation (6688 ms), save_wall_photo and wall.post not reached. No transport exception/VK error code was reported. The first response body was neither persisted nor printed, so its exact invalid field cannot be reconstructed. This does not prove the cause of the historical three failures.
2. A bounded upload-only diagnostic of the same JPEG succeeded. Safe shape metadata confirmed server=int, photo=str/non-empty, hash=str/non-empty. No response values, upload capability URL or token were logged. saveWallPhoto created user-owned photo 615459987_457239320. No post was sent by this probe.
3. Explicit operator recovery of the same known pre-wall failed Submission preserved its guid and cumulative attempt budget. It cleared only the known pre-wall failure, retained the same campaign/community/media, and accounted for the probe as attempt 2. The final sender tick was attempt 3: fresh upload succeeded (get_upload_server 240 ms, multipart HTTP 200 1321 ms, save_wall_photo 94 ms), then wall.post ran exactly once. No automatic write retry loop was run.

No deterministic upload implementation bug was established; no speculative transport/parser changes were made. Added fixed safe stage diagnostics (success, HTTP status where known, transport class, VK code, duration, protocol/error class). API successes report HTTP 200; API failures without transport status expose null rather than guessing. Response bodies, cookies, tokens, hashes/capability URLs and exception messages are excluded. Existing bounded pre-wall retries and durable wall boundary are unchanged.

Receipt: `6`; readback `-242100737_6`, author 615459987, post_type=suggest, canonical photo `-242100737_456239019`, audio `2000410139_456245636`. Existing record_suggested_submission completed: status=submitted, phase=verified, submitted_at=2026-10-10T00:19:28Z.

Existing read-only PublicationMonitor returned pending / submitted, published_post_id=null. Suggestion was not manually accepted.

## Exact live write counts

This task only: photos.getWallUploadServer=3, multipart=3, photos.saveWallPhoto=2, wall.post=1. Thus six VK API write-method calls plus three multipart uploads; one suggested post. The upload-only probe left one user-owned saved photo unattached; no cleanup/delete write was performed.

VK_WRITE_ENABLED was restored to false and allowlist emptied after each controlled operation. API and read-only reference worker remain write-disabled; no sender daemon was started. Ignored local Pinterest publication opt-in remains true under operator control; committed/default false. Real grid 325b7475-0c97-442e-aaee-125d70a73e50 (514 available communities) was not sent.

## Validation

Backend: 631 passed. Frontend: 66 passed; typecheck and production build passed. Ruff and mypy (82 source files) passed. Browser review: Next community preserves grid/review context, 0/6 reviewed without automatic ratings, no JavaScript errors and no mobile overflow. All six saved Photo Lab jobs are ready. Unrelated backend/pyproject.toml edit is preserved.

Review the six ready communities in [local Photo Lab](http://localhost:5173/communities/746254f7-00cb-4b56-b4e8-7cce35366421?grid=325b7475-0c97-442e-aaee-125d70a73e50&review=746254f7-00cb-4b56-b4e8-7cce35366421,f6649c13-484d-4968-a900-32a430909c1c,4bc998b0-fb50-41a8-8b51-4e6654903b58,e82138f1-325d-48a6-adf5-db2f6f85c847,4a6c9b25-aad9-4fcb-b2de-5a30f6b213b4,3c8f9751-4fad-41f5-8e64-c7d93f3bc443).
