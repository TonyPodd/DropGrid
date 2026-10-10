# Web workflow: campaign preparation

Grid import → Campaign draft → **Подготовить кампанию** → optional photo review → sending → monitoring/results.

The product preparation action performs bounded read-only VK warmup and shared photo planning.
It needs a validated Account; it never sends posts. The legacy `/prepare` endpoint only creates
local Submission rows. See [campaign preparation, review and account pools](campaign-product-workflow.md).

## Import a grid

Open Grids → Import. Paste domains/links, optional numbered lines and category
headings, then click «Разобрать сетку». Preview shows all valid rows, category
counts and original invalid/duplicate lines with their line numbers. For ambiguous
category headings use `# Category`.

Correct the input and parse again, or enter a name and save only valid rows.
When errors exist, a dialog confirms exactly how many rows will be skipped.
Editing source text invalidates the preview. No valid communities means no save.

Grid details shows category counts and paginated communities. The category belongs
to **GridCommunity**, so importing a community under another category in another
grid cannot change the earlier grid/campaign view. Communities shows the global
catalog's initial category. No page opening automatically resolves VK identities.
A stored numeric VK ID is shown as Resolved; this is not a fresh live verification.

## Create and edit a draft

Open Campaigns → Create (or create from Grid details). Choose a grid, enter a name,
VK audio URL, optional unchanged caption and publication checking window (default
72 hours). Grid selection pages through the catalog rather than downloading all
communities. Audio preview calls `/tracks/parse`, the existing **pure** audio parser;
there is no VK request. POST/PATCH uses the same parser and derives persisted
track owner/audio IDs. Independent supplied IDs cannot override the parsed identity.
Malformed links return 422 without echoing the input.

The positive PostgreSQL integer range `1..2147483647` remains the hours limit on
both client and server. No 720-hour product policy is introduced while monitoring
is unimplemented. Only DRAFT can be edited. Grid is fixed after creation by the
existing API contract. READY and later campaigns cannot change draft fields.

## Prepare and inspect

On a draft, click Prepare campaign and confirm the number of submissions. It
creates one submission per grid community and changes status to READY. Repeated
or concurrent API prepare calls remain idempotent and preserve existing history.
No Start/Send/Run action is exposed.

Campaign details displays aggregate status counts and paginated submissions
(default 25, maximum 200). Filter by status and by grid membership category;
changing a filter resets to page 1. Empty account/media assignments are normal.
Published links are limited to HTTPS VK wall URLs. Legacy free-form error messages
are replaced by a fixed safe message; only numeric error codes are retained,
other codes display ERROR. Raw tracebacks and potentially secret-bearing text
are never returned in this view.

Accounts Validate is an explicit, existing read-only VK action. If credentials
are unavailable, UI explains that configuration is missing. It is not required
for the workflow and was not invoked against live VK during this stage.

## API additions

- `GET /api/v1/dashboard`: catalog counts, grouped campaign statuses, 8 recent campaigns.
- `GET /api/v1/grids`: community/category counts added; existing limit/offset retained.
- `GET /api/v1/grids/{id}`: category summary, total count and bounded embedded communities.
- `GET /api/v1/grids/{id}/communities?page=1&page_size=25`: membership-category rows and total.
- Campaign list/detail: grid name, grid community count, submission count added.
- `POST /api/v1/tracks/parse`: pure audio identity preview.
- `GET /api/v1/campaigns/{id}/stats`: all submission status counts in a grouped query.
- `GET /api/v1/campaigns/{id}/submissions`: page/page_size/status/category; empty category
  selects uncategorized members, omitted category means all.

Counts and row displays use grouped SQL and joins; no query per rendered row.
There are no database migrations or changes to VK integration code. Existing campaigns
are not backfilled: old records may retain null track IDs until their draft track
is explicitly updated. Newly created campaigns always persist parsed IDs.

## Verification

Backend: pytest against an isolated migrated PostgreSQL database, Ruff, mypy,
pre-commit and OpenAPI schema generation. Frontend: `npm ci`, `npm run typecheck`,
`npm run test` (Vitest/Testing Library) and `npm run build`.
