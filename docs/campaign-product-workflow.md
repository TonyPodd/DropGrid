# Campaign preparation, review and account pools

Connect Accounts with an externally obtained token. `users.get` validates identity before
creating/reusing an Account; token encryption is bound to that Account UUID. Duplicate VK
identities reuse the row. Disabled identities require explicit enabling. Tokens are never
returned to the UI. Gender and per-campaign quota can be edited on Accounts.

Create a campaign with AUTO or REVIEW_BEFORE_SEND. Select usable Accounts and click
**Подготовить кампанию**. The reference-study worker creates submissions, checks community
readiness, warms up bounded recent compatible CORE references, invokes the shared photo
planner, stores selected media and allocates Accounts. A durable job recovers progress on
reload. No preparation/review endpoint calls a VK write method.

AUTO requires no per-community preview. REVIEW_BEFORE_SEND enters `awaiting_review`.
Review the selected photo, inspect alternatives or attention flags, then confirm one or all
prepared selections. Replacement stores a proposal; only confirmation creates a final
operator decision. Sender preflight blocks unconfirmed intended rows. The standalone
Community Photo Lab **Подобрать фото** warms references and discovers/ranks with one click;
its feedback is separate from campaign assignment.

Quota is an operational setting, not a guarantee from VK:
`ACCOUNT_CAMPAIGN_SEND_QUOTA=100`, with Account overrides. Allocation follows the grid's
category distribution (below) and fills accounts one at a time in pool order. 250 jobs
with three quota-100 accounts fit (100/100/50); 314 leave 14 unassigned and block start.
Changing the pool is unavailable after sending starts. Submission.account_id remains bound
through receipts, unknown write outcomes and publication monitoring. Existing global sender
serialization, per-account pacing, VK write flag and allowlist remain unchanged.

Activity reports preparation and account progress. Campaign results show category/source/
Account breakdowns and published/sent acceptance rate (pending moderation is in the sent
denominator). Results do not feed ranking.

## Category gender distribution

The **Categories** tab lists the categories detected in an imported grid and places each
one into one of three columns: male, female or unisex
(`GET/PUT /api/v1/grids/{id}/category-genders`). A save must place every current grid
category exactly once; the UI asks for a column for each category left unplaced.
Categories with the same name in other grids are offered as suggestions
(case-insensitive) but are never saved implicitly.

Allocation takes accounts in pool order (the selection order in the campaign). Each account
first receives its own gender's categories, then unisex ones, up to its quota; only then does
the next account receive what is left. Accounts without a gender receive unisex categories
only. Insertion order is the send order and is stored as `Submission.send_order` at start.
A grid category without a saved column blocks preflight (`category_unassigned`,
`unassigned_categories`) until it is distributed; the readiness report counts it too.
Changing a distribution affects campaigns at their next preparation/start; started
campaigns keep their persisted assignment.

## Learning from explicit review

At most 12 candidate feature snapshots per selection session: scores, provider, context and
stable image identifiers. No embedding vectors. Only displayed candidates are comparison
negatives; hidden candidates, unconfirmed proposals and automatic choices produce no final
preference labels. Existing likes/dislikes remain separate, lower-priority signals. Publication
outcomes are never operator preferences.

`PHOTO_LEARNED_RANKER_MIN_CHOICES=100`, retrain after
`PHOTO_LEARNED_RANKER_RETRAIN_CHOICES=25` additional confirmations, or explicitly update.
The worker fits a small CPU pairwise logistic linear model on current features. Older 80%
train; newer 20% validate. Metrics include baseline/learned top-1 agreement, pair counts,
schema version and sample counts. The model starts in SHADOW; it cannot affect selection
unless it outperforms the baseline on at least 20 held-out decisions **and** the operator
explicitly enables it under Advanced. Missing/corrupt models fall back to deterministic
ranking. Every assignment stores its ranking model version. CLIP and baseline weights are
unchanged; no per-community models or external ML services.

## Validation

Migration: `cd backend && uv run alembic upgrade head`.
Run the API and `python -m dropgrid.photos.reference_worker` with the same database/media.
Frontend: `cd frontend && npm run dev`. Keep `VK_WRITE_ENABLED=false` and an empty
write allowlist during local preparation/review tests. Do not start the real grid as a test.
