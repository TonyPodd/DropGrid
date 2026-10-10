# Human photo validation

`/review/:batchId` is the focused reviewer interface. It shares the existing
PhotoSelectionSession/PhotoSelectionCandidate snapshots; membership only stores
IDs, position, state, automatic rank and reviewer timestamps. There is no second
candidate dataset and no dependency on localStorage, browser profile or VK Account.

## Operator workflow

Owner: Campaign → Проверка фото → create/open validation batch. Default target is
150 (bounded to 1–200 and available prepared snapshots). Creation is deterministic:
category, winner provider, content hint, attention, score margin, provider diversity.
Up to one quarter prioritizes Pinterest + VK category competition; remaining slots
balance coverage across six axes. Close margin is ≤0.03, strong Pinterest competition
is a VK-category winner with a Pinterest final score within 0.10. These thresholds
only describe sampling; they never change production scores or the chosen photo.

Reviewer: choose/create Tony or Tima, then Оставить or open Альтернативы, select
and press Выбрать/Enter. Skips do not create preference labels. Enter confirms,
1–9 selects an alternative, arrows navigate, D records weak dislike, S skips,
Space enlarges. Typing, modifiers and held-key repeats do not confirm photos.
The progress counters, cursor, filters and provisional choices are server-persisted.
Reload resumes the same item. Previous confirmed choices can be amended only while
batch is open, submission pending and campaign has not started sending.

A decision remains one PhotoSelectionSession: editing replaces chosen_rank and
selected flags, updates reviewer/timestamp, and does not append a second training
example. Only candidates actually exposed by the UI are marked displayed; merely
prefetched images are not evidence. Closed alternatives do not become negatives.
AUTO choices, skips and publication outcomes are not manual labels. Training still
requires 100 confirmed choices and normal retraining interval 25. No per-reviewer
models are introduced. Owner diagnostics and model metrics group by reviewer.

New production snapshots reserve the automatic winner and best available candidate
from each provider, then fill with global alternatives, bounded to 12. Existing
snapshots are not expanded or re-ranked: diversity can be limited by their historical
contents. Missing competition is reported rather than filled with invented candidates.

## Failures and retries

Owner sees the failed count and Повторить N. The durable production queue captures
only failed pending submission IDs; successful rows are not re-prepared. Read-only
context, reference/discovery/ranking/import pipeline and concurrency bounds are shared
with ordinary preparation. Known safe reasons replace generic preparation_error:
no_candidates, no_references, materialization_failed, dedup_exhausted,
cooldown_exhausted, download_error, storage_error, category_archive_error,
pinterest_unavailable, other. Provider warnings remain separate. A retry may remain
unassigned when no viable sources exist; it does not relax reuse, licensing or ranking.

Photo Lab remains the owner tool for investigating one community. Review is human
validation of a bulk preparation snapshot. Retry/account/sender/model controls do
not appear on the reviewer screen.

## Server migration checklist (not deployed in this task)

- Run the frontend, API, Postgres and preparation/reference worker on one instance.
- Apply migrations, back up Postgres including batches, reviewers, cursors,
  snapshots, preferences, imports and caches; preserve APP_SECRET_KEY privately
  alongside encrypted account credentials. Never include secrets in exports/logs.
- Mount the same persistent media root into API and worker. Current local runtime:
  Docker volume `dropgrid-photo-live_media_data` mounted at `/app/media`.
- MediaAsset files: `<MEDIA_STORAGE_DIR>/<storage_key>`; normalized asset keys use
  SHA-based subdirectories. VisualLibrary embedding metadata is in Postgres.
- Reference photos / own and category alternative previews:
  `<MEDIA_STORAGE_DIR>/references/<storage_key>`.
- Pinterest candidate previews:
  `<MEDIA_STORAGE_DIR>/previews/pinterest/<storage_key>`.
- Other provider candidates use their imported MediaAsset content; archive candidates
  use reference content. No separate browser image cache is required for correctness.
- CLIP model currently `/app/media/models/clip-vision-int8.onnx`; preserve compatible
  model/version and database embeddings. Do not clear Pinterest/reference caches.
- Serve all content endpoints and frontend through the same HTTPS origin; configure
  frontend API URL, CORS, persistent volumes and backups. Verify thumbnail URLs,
  reload recovery and media checksums before sharing.
- Reviewer identity is attribution, not authentication. Add owner/reviewer access
  control before publicly exposing API/account/admin operations; no auth system or
  deployment is added by this task.
- Keep VK_WRITE_ENABLED=false, allowlist empty, sender stopped throughout shared
  validation. Review confirmations only materialize/reuse local images and store
  preference evidence; no VK upload or post method is required.
