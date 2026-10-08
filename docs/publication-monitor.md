# VK publication monitoring

The worker performs VK reads only. It does not send campaigns, upload photos,
join groups, mark notifications viewed, or acquire credentials. It uses imported,
encrypted Account credentials through DBTokenProvider and the existing VKClient
rate limiter. A missing or unusable credential leaves the submission unresolved.

## Recording a successful suggestion

Future sending code can call `record_suggested_submission(session, submission,
account_id=..., receipt=..., suggestion=...)` inside its own transaction, after
an actual successful receipt and read-back. This service makes no VK calls.
It verifies the receipt, community and author's Account identity, then records
the group-owned canonical photo from read-back, not the user-owned upload result.
It increments assigned MediaAsset usage exactly once in the same transaction.
Repeated identical receipts are no-ops, including after publication. Conflicting
receipts are rejected. Registration and final publication commits serialize on
the Account row; no raw post body or notification JSON is stored.

## Evidence order

1. Poll `notifications.get(filters=["wall"])` once per eligible Account.
   Only `wall_publish` is processed; unknown types remain valid and are ignored.
   The observed `feedback.id` is the published ID and photo `post_id` is the
   suggested ID. Match Account, community, suggested ID and canonical photo.
   A receipt must have exactly one candidate Submission. Duplicate receipts and
   conflicting published IDs are reported without overwriting publication.
2. Confirm with `wall.getById`: exactly one object, expected ID/owner, `post_type=post`,
   canonical photo and a valid publication timestamp at/after suggestion time.
   The final commit rechecks identity and uniqueness. Author, text and audio can
   change under moderation; they are supplementary evidence, not prerequisites.
3. Fallback checks the old suggestion ID. A full `suggest` stays submitted,
   including after the horizon. A verified same-object `post` is accepted.
   Missing/degraded objects trigger a bounded normal-wall scan. Only one exact
   canonical-photo candidate in a completed time window can be confirmed; it is
   read again through `wall.getById` before publication is committed.

Use the **published** ID in `https://vk.com/wall-{group_id}_{published_id}`.
The regression fixture covers real suggestion 4 → published post 5, canonical
photo -242100737_456239018 and audio 2000410139_456245636.

## Windows, scheduling and concurrency

Polling and fallback checks normally run every five minutes. Notification windows
have a five-minute overlap; timestamps are not unique event identifiers. The first
window includes the earliest submitted receipt or the last 24 hours, whichever is
older. Up to three notification pages are processed. The Account cursor advances
only after complete successful processing. Failure, repeated cursors, pagination
limits or unresolved relevant events retain its previous timestamp.

Fallback scans up to three pages of 100 normal-wall posts. It requires exhaustion
of the reported items or a chronologically ordered non-pinned page reaching before
suggestion time. Unordered/undated results, multiple candidates and incomplete
windows remain unresolved. Photo identity anchors reconciliation; a matching audio,
text fragment, author or notification alone cannot publish a submission.

`Campaign.publication_check_hours` (default 72) is the maximum reconciliation
horizon before a final absence decision, not a deadline that automatically fails
pending suggestions. `not_found` requires no clearly pending suggestion, successful
notification/window processing and a completed wall reconciliation with zero exact
candidates. Incomplete reads remain submitted and rescheduled.

PostgreSQL `FOR UPDATE SKIP LOCKED` claims and 15-minute UUID leases protect Account
polls and Submission checks. Every VK call occurs outside DB transactions. Submission
commits are fenced by the same unexpired lease token, so abandoned/stale work cannot
commit. A worker cycle selects up to five eligible Accounts and ten due submissions
per Account; cursors/check times prevent hot-loop polling. Publication detection does
not increment media usage. Safe evidence contains only fixed labels, booleans and
numeric codes/counts. The API filters these fields again before returning evidence.

## API and UI

- `POST /api/v1/submissions/{id}/check-publication`: notification poll then fallback;
  returns previous/current status, suggestion state, safe evidence, published ID/URL
  and check time. It can update local DB state but performs no VK writes.
- `GET /api/v1/campaigns/{id}/published?limit=100&offset=0`: accepted posts with
  community, grid category, published URL and publication time. No VK calls.
- Campaign submissions show «На модерации», «Опубликовано», «Не найдено», publication
  time/URL and an explicit manual check button. Temporary read errors do not become
  publication failures.

Apply the additive migration before running the updated backend/worker:

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/python -m dropgrid.worker
```

The migration preserves legacy rows with nullable new receipt fields. Such rows
cannot be inferred safely and remain unresolved until actual receipt evidence is
available. The notification delivery mechanism is not assumed to be exhaustive or
durable; the independent wall fallback is required. This change does not establish
notification coverage for suggestions without canonical photos.
