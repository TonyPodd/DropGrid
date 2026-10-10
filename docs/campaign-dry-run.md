# Campaign readiness dry run

A dry run creates a **dedicated, permanently non-sendable** campaign. It uses the
same preparation service, photo planner, ranking, assignment and review snapshot
code as normal campaigns. VK context reads, public image downloads and local
MediaAsset imports are allowed. No sender is instantiated by preparation.

```http
POST /api/v1/campaign-dry-runs
Content-Type: application/json

{
  "grid_id": "<existing-grid-uuid>",
  "name": "Readiness dry run",
  "track_url": "<VK-audio-url>",
  "account_ids": ["<validated-account-uuid>"],
  "photo_review_mode": "AUTO"
}
```

The available, resolved, active community IDs are captured at creation. Optional
`community_ids` selects a bounded review sample within that grid. Unavailable
rows are counted in the report but excluded from the immutable dry-run scope.
Do not convert an existing sendable campaign into a dry run.

The Campaign page offers **Создать отдельный dry run** and links to the created
campaign. Reload recovers persisted progress. Warmup runs at most two communities
at a time, capped by `MEDIA_PREPARATION_CONCURRENCY`; VK reads, downloads and CLIP
retain their existing limits. Photo planning commits batches of three submissions,
with campaign-wide rotation counts preserved across batches. A failed batch is
recorded locally and remaining batches continue. Restarted workers reclaim expired
leases and preserve previous assignments.

```http
GET /api/v1/campaigns/<id>/preparation-workflow
GET /api/v1/campaigns/<id>/readiness-report
GET /api/v1/campaigns/<id>/photo-review
```

Reports include grid/scope sizes, prepared/failed/pending counts, sources, attention,
Pinterest fallback/unavailability, unique assets, repeated assignments, maximum
reuse, near-duplicate exclusion events, elapsed time and usable Account capacity.
`pinterest_fallback_count` counts non-Pinterest selections where Pinterest was
unavailable or displayed Pinterest alternatives were not selected. It does not
label every internal-library selection as a failed Pinterest request.
Near-duplicate exclusions count filtering events, rather than distinct images,
because a candidate may be examined in more than one pool stage.

The API rejects campaign start for `is_dry_run=true`; this flag cannot be changed
through the campaign patch API. Preflight cannot report it as send-ready. Sender
claim queries also exclude it. Preparation has a task-local read-only guard at
both official VK writes and the uploader's entry point, even if outer settings
permit writes. It blocks `photos.getWallUploadServer`, `photos.saveWallPhoto`,
`wall.post` and multipart upload. Keep `VK_WRITE_ENABLED=false` during readiness.

## Selected category archive imports

`CROSS_COMMUNITY_REUSE_ENABLED` admits compatible category candidates into the
same source-neutral ranking. Shortlisting and reference-image caching do not
create MediaAssets. Only a selected assignment or explicit review confirmation
imports/reuses a MediaAsset. Unselected candidates remain bounded reference rows.
Provider identity, SHA and perceptual near-duplicate checks reuse an existing file.
Source post age is provenance; target CommunityMediaUsage controls the 180-day
cooldown. It never uses source-community usage as target-community history.

New MediaAssets retain provider `vk_category_archive`; if dedup reuses a different
provider's asset, MediaProviderImport preserves the category alias. Imports retain
source community/post, canonical VK photo owner/id, original posted_at, SHA,
pHash and source embedding/model/dimensions. `source_embedding` stores the original
binary embedding as hex text; the canonical MediaAsset retains its binary vector.
Existing same-community opt-in/age policy remains in force for `vk_archive`.

## Manual ranking collection

Use a dedicated `REVIEW_BEFORE_SEND` dry-run sample. Inspect real photos normally,
choose alternatives and confirm only your actual preferences. AUTO proposals and
unconfirmed choices do not become final labels. Only confirmed, actually displayed
alternatives form comparisons; hidden candidates never become negatives.

The compact Campaign dashboard shows confirmed decisions / 100 and latest model.
Advanced shows temporal training/validation sizes, baseline and learned top-1
agreement and promotion readiness. Defaults remain 100 choices and retraining
interval 25. A model is shadow-only until it passes validation and the operator
explicitly enables it. Preparation and publication outcomes do not train it.
