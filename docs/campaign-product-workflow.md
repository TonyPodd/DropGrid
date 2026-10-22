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

## Dating and selfie publics (girl photos)

Grid categories whose name contains «знакомств» or «селфи» (also `selfie`/`dating`) are
persona categories. They need no gender column in **Categories**. Before preparation the
campaign counts these publics and shows ⌈publics / quota⌉ girl-account slots (1 account =
`ACCOUNT_CAMPAIGN_SEND_QUOTA`, 100 publics). Each slot takes an already connected account
or a new one connected by token right there (`/accounts/connect`; marked female when it
has no gender). Each girl account has any number of selfies and other photos, at least
one of each and at most 30 (`POST /accounts/{id}/persona-photos?kind=selfie|other`: raw
JPEG/PNG/WEBP, optional tags such as «в зеркале»; `PATCH`/`DELETE .../{media_asset_id}`
moves a photo between kinds, retags or removes it; selfies are always tagged «селфи»).
Preparation is blocked until capacity covers every public and every girl has both kinds.

A girl's photo belongs to her account (one VK token) only: `account_persona_photos` has a
unique `media_asset_id`; uploads reject a photo linked to or already sent by another
account (`persona_photo_taken`), a photo she already has (`persona_photo_duplicate`),
library photos (`persona_photo_in_library`) and a 31st photo (`persona_photo_limit`).
Uploads use provider `persona`, never offered to other categories. Removing a photo keeps
its MediaAsset for history.

Allocation puts girl accounts' lane first: their dating/selfie publics, then their own
gender and unisex categories. Other accounts never receive persona publics. A public that
already holds one girl's photo stays with her (pinned), so pilot scopes cannot swap
girls. Photo choice (`photos/persona.py`) ranks only the allocated girl's photos per
public: 70% CLIP similarity to the public's core wall references, 25% tag matches in its
name, VK description and grid hints, 5% spread across the campaign; a photo the public
already received in the cooldown window goes last. Choices are snapshotted like library
choices, so REVIEW_BEFORE_SEND works unchanged; confirming a girl photo skips the library
reuse limit and community cooldown. Preparation reads the VK description once for persona
publics that have none yet.

Preflight blocks start on `persona_account_missing`, `persona_photos_incomplete` (a girl
without a selfie or without another photo) or `persona_photo_mismatch` (any row whose
girl photo is not the allocated account's). The sender re-checks ownership before any VK
call and skips such a row as `persona_photo_mismatch`, so a girl's photo is never sent
with another token.

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
schema version and sample counts.

There is one working model, the **champion**; there is no manual baseline/learned switch.
Each training produces a challenger. Challenger, champion and the deterministic baseline
are scored on the same newest validation decisions, which neither model was trained on.
With at least 20 of them the outcome is: `promoted` (challenger beats the baseline and is
at least as accurate as the champion; it becomes the champion), `kept` (the champion stays
and records its fresh accuracy), `fallback` (the champion fell behind the baseline and is
retired; the baseline ranks until a challenger beats it) or `not_better` (no champion and
the challenger does not beat the baseline). With fewer, nothing changes. Replaced
champions become `retired`. Without a champion the worker evaluates once at start, so a
model that passed the former manual check competes right away. Missing/corrupt models fall
back to deterministic ranking. Every assignment stores its ranking model version. CLIP and baseline weights are
unchanged; no per-community models or external ML services.

## Validation

Migration: `cd backend && uv run alembic upgrade head`.
Run the API and `python -m dropgrid.photos.reference_worker` with the same database/media.
Frontend: `cd frontend && npm run dev`. Keep `VK_WRITE_ENABLED=false` and an empty
write allowlist during local preparation/review tests. Do not start the real grid as a test.
