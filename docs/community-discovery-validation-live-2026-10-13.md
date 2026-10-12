# Community Discovery validation rollout — 2026-10-13 (Moscow)

## Real batches

- Initial: **Discovery validation v1**, 0/66 reviewed, Tony 0 / Tima 0.
- Review: https://45.147.177.46/discovery/review/b43c2c87-e925-4554-b27b-c78a0ce51b27
- Expansion: **Discovery validation v1 — expansion**, 0/137 reviewed.
- Expansion review: https://45.147.177.46/discovery/review/d16e5fc9-8f8e-40ec-92d5-49e326943945
- No real labels, revisions, audit events or automatic Grid additions were generated.

The initial cohort was frozen before expansion: 40 machine-recommended and 26 needs-review.
Categories: РЕГИОН 03 / Buryatia auto 24, ГРУППЫ СОНИ / BMW E60 26, ТРАКТОР 16.
Observed regions: Buryatia 24, Rostov Oblast 3, Krasnoyarsk Krai 1, Vologda Oblast 1, unknown 37.
Unknown candidate geography is distinct from the existing Grid’s zero unknown region codes.

## Sequential bounded live pilots

All five used the existing DBTokenProvider and production read-only discovery worker. No parallel runs.
Three queries per run; existing maximum 30 candidate analyses, 20 visual analyses, eight previews
and bounded download attempts per candidate. VK-read and visual concurrency remained one.

| Cluster | Seeds | Raw | Bounded deduplicated | New | Recommended | Needs review | Filtered | Visual | Seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| regional_komi | 3 | 90 | 30 | 30 | 15 | 15 | 0 | 20 | 213.8 |
| regional_tuva | 2 | 90 | 30 | 24 | 9 | 15 | 5 | 16 | 144.4 |
| regional_saratov | 3 | 90 | 30 | 30 | 19 | 11 | 0 | 20 | 279.7 |
| jdm | 19 | 90 | 30 | 27 | 18 | 9 | 0 | 20 | 216.7 |
| cats | 3 | 90 | 30 | 26 | 19 | 7 | 4 | 20 | 225.6 |

Queries and run IDs:

- `regional_komi` / `362e50b9-ac6e-4648-a048-506b969be53f`: авто сыктывкар; автоклуб сыктывкар; 11 регион авто.
- `regional_tuva` / `21392b6d-c0d4-4cc1-b311-e7e7dca0690f`: авто кызыл; автоклуб кызыл; 17 регион авто.
- `regional_saratov` / `faac2419-7117-4b81-bfe0-ae5e9d33973c`: авто саратов; автоклуб саратов; 64 регион авто.
- `jdm` / `3a50fccd-c63c-49e4-98a7-45ff9ea9684d`: jdm; jdm club; jdm россия.
- `cats` / `794f81bf-b58a-4c40-81ad-8f96535ac29d`: кошки; котики; кошки клуб.

Run “new” counters are per-run discovery outcomes; the second batch contains unique newly eligible
candidates excluding the original 66. Final Inbox: 203 new, 120 recommended, 83 needs-review,
zero human-approved, rejected or Grid-added. All pilot known-region mismatch counters were zero.

## Human measurement and UX

Precision, approval rate, precision@5/10/20, agreement, score/evidence/region buckets and hypothetical
threshold error estimates are N/A until actual labels exist. No machine recommendation is a label.
Uncertain is excluded from binary training; original top-K must be completely decisively reviewed.
No ranker training or automatic score/weight changes are enabled.

Tony and Tima have equal access and independent attributed labels/cursors. Focused review provides
safe VK links, cached full-frame photos with enlargement, optional persisted rejection reasons,
A/R/U/G/Left/Right shortcuts, filters, reload resume, revision with audit history and frozen snapshots.
Human approval does not add membership. Secondary Add requires a real latest approved label and
explicit confirmation, retaining candidate/run/reviewer/timestamp/category/region provenance.
Restore requeues a candidate but does not erase historical human outcomes or training snapshots.

## Cache and resources

- Host sampled idle RAM: 979 MiB; sampled peak: 1605 MiB.
- Minimum available RAM: 356 MiB; 217 samples during rollout/pilots.
- Peak was 13 MiB above the previous approximate 1592 MiB baseline; concurrency did not increase.
- API and reference-study OOM=false, restarts=0 during pilots; visual worker cap remains 640 MiB.
- Final discovery cache: 314,569,155 bytes (300.00 MiB), 418 files.
- Hard cap: 300 MiB; TTL: 30 days; oldest file: 2026-10-12T20:05:57.355549+00:00.
- Cumulative evictions since this migration: 629. Older evictions are not reconstructed.
- Original batch preview keys: 265; initially available 252, finally 254.
- Some previews were already absent before rollout; expired-photo placeholders are explicit.
- Available open-batch previews receive eviction priority; cap/TTL still take precedence.
- Media disk: 4.4 GiB; host disk: 28 GiB, 14 GiB used, 15 GiB available (49%).

## Preservation and safety

- Existing Grid: 521 memberships unchanged; prepared campaign scope: 514 unchanged.
- Existing real photo batch, items, selections, candidate rows, submission-media assignments and
  MediaAsset count have identical before/after fingerprints.
- Existing Accounts, ignored APP_SECRET_KEY, Postgres volume, media, TLS, backups and other VPS
  services preserved. Additive migration applied; no database replacement or data reset.
- Existing photo review works for Tony/Tima at desktop/mobile; group link, full-frame images,
  attribution and reload verified with real GET-only QA. Keyboard/mutation checks used fixtures.
- Region policy unchanged: same-region cross-community preferred; wrong region forbidden;
  unknown fallback marked; own-archive minimum remains 365 days.
- VK writes: 0. VK_WRITE_ENABLED=false, write allowlist empty, sender container absent.
- Scheduled discovery remains disabled. No new Submission was created during this milestone.

## Validation

- Backend full suite: 750 passed; frontend full suite: 105 passed.
- Ruff lint/format, mypy (105 source files), Alembic check, frontend typecheck/build, pre-commit passed.
- Browser fixture mutation QA at 1365px/390px: approval/rejection reason/uncertain, auto-advance,
  filter/resume, contained photos/enlargement; three synthetic labels only.
- Production browser QA uses GET only and blocks any non-GET API request; no real decisions clicked.
- Regression covers frozen originating preview identity before and after the first label.
- Five pre-existing unrelated local user edits were preserved and excluded from deployment.
