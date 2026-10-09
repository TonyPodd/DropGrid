# Campaign Sender v1

Prepare the Campaign and its media plan, select an active Account with an imported
DB token, then inspect `POST /api/v1/campaigns/{id}/preflight` and call `/start` with
`{"account_id":"UUID","max_submissions":1}` for a pilot. Omit `max_submissions`
(or use null) for the whole prepared Campaign. The first N eligible communities
are selected in domain/Submission UUID order; the rest become skipped with
`pilot_scope_excluded`. Unavailable communities and gender mismatches are skipped.
Every intended eligible row must have a valid, enabled, assigned local JPEG whose
hash and dimensions agree with MediaAsset. Preflight never contacts VK. Start
only queues work; it does not enable writes or start a process.

The dedicated process is `python -m dropgrid.worker.sender`. With Docker:

```bash
docker compose --profile sender up -d sender
```

It shares the API's media volume and encrypted DB credentials. It exclusively
uses DBTokenProvider; no fallback to a diagnostic/environment token. The usual
worker remains the independent Publication Monitor. Both workers refresh
Campaign lifecycle; sender upload latency does not block publication reads.
All writes require `VK_WRITE_ENABLED=true` (default false). A nonempty
`VK_TEST_ALLOWED_COMMUNITY_IDS` additionally restricts sender targets, allowing a
controlled test with exactly `242100737`. With an empty allowlist, explicitly
starting a Campaign and enabling writes permits its validated scope; the test
allowlist is not a production account rotation system.

## Durable boundaries

`queued → claimed → photo_uploaded → wall_post_started → receipt_received →
verified`. `readback_pending` is a read-only recovery branch after receipt.
Stable `guid` is the Submission UUID, persisted at start and sent as a string per
[official wall.post schema](https://github.com/VKCOM/vk-api-schema/blob/master/wall/methods.json).
It is additional protection, not a guarantee of exactly-once remote delivery.

Each Submission uploads a fresh photo via WallPhotoUploader. VK can return a
user-owned upload and copy it to a different group-owned photo in the suggestion.
The exact receipt read-back supplies the canonical identity; uploaded identity
is retained separately and is not required to equal the canonical copy. Local
assets can be reused; remote photo attachments cannot. Caption is optional;
attachments are photo then the Campaign's already parsed audio.

PostgreSQL `FOR UPDATE SKIP LOCKED` claims have 15-minute fenced leases. A dedicated
AUTOCOMMIT connection holds a session advisory lock for the pipeline, bounding
all sender processes against the same DB to one concurrent send, including across
Accounts. No DB transaction remains open across HTTP. Persisted per-Account
`vk_next_send_at` adds conservative pacing (`VK_SEND_INTERVAL_SECONDS=60`).
Cancelled campaigns cannot cross a new wall-post boundary. A boundary already
committed before cancellation represents an in-flight operation and cannot be
undone; remote photos/posts are never deleted by cancellation.

Pre-wall transient transport/rate failures retry on later cycles at most three
upload attempts, each with a fresh photo; abandoned photos may remain. Invalid
media, authorization/permission failures and definite API rejections are terminal.
`wall.post` has exactly one application attempt, with the existing client's
single-attempt write semantics. Persist `wall_post_started` BEFORE HTTP. Transport,
protocol errors or a crash before receipt commit leave `wall_post_outcome_unknown`.
They remain unresolved/sending, keep the Campaign running and NEVER automatically
write again. Even a crash immediately before the HTTP call is conservatively
quarantined. Manual read-only investigation is required; no automatic reset or
retry-write endpoint is provided.

Receipt is committed BEFORE exact `wall.getById`. Recovery uses the receipt,
group/account, exactly one canonical group-owned photo and expected audio identity. Six bounded
read-back cycles use exponential backoff; existing VKClient may retry individual
reads. Exhaustion leaves `suggestion_readback_exhausted` for manual investigation;
it never marks the remote write absent or resends. Read-back works with writes
disabled and after Campaign cancellation. It calls the existing
`record_suggested_submission`, so asset/community usage is recorded once and the
existing Publication Monitor owns all subsequent publication detection.

`running`: any pending/sending row. `monitoring`: sends finished and suggestions
remain submitted. `completed`: no nonterminal rows and at least one
published/not_found result. An all-failed/skipped Campaign becomes `failed`.
`POST /campaigns/{id}/cancel` accepts ready/running/monitoring and preserves remote
writes and monitor recovery. The UI shows preflight, pilot scope, explicit
confirmation, safe error codes and status counts; running/monitoring refreshes
read-only every ten seconds.

A live smoke must use a separate one-community Campaign, exact test allowlist,
one invocation of this SAME sender and restore writes=false/allowlist empty. Never
start the 521-community grid as part of a test. Tokens, upstream payloads, upload
capability URLs and raw HTTP exceptions must not appear in logs or API output.

## Controlled local smoke, 2026-10-09

All 569 backend tests (including 46 sender tests), 53 frontend tests and quality
gates passed. DB-token read capabilities for Account VK user 615459987 and target
242100737 succeeded. Only a dedicated one-community Campaign was started:
`880e9cf2-1e91-4075-8158-954d513210b7`, Submission
`c766faf6-f854-4303-8919-ae0fe6ac3e7f`, prepared MediaAsset
`d5156b7e-ebac-4445-8eb7-900195049935`.

The live smoke remains **unverified**: three pre-wall upload attempts ended in
`send_preflight_failed`; the first was a transient multipart transport failure.
A local canonical-photo validation bug was corrected and regression-tested before
the final attempt: uploaded user-owned identity must not be equated to VK's
canonical group-owned copy. No wall-post boundary was crossed, no receipt exists,
and no usage was recorded. Final Submission/Campaign status is failed. Existing
Publication Monitor handoff could not be exercised without a suggestion.

Total attempted remote operations: `photos.getWallUploadServer` 3, multipart
upload 3, `photos.saveWallPhoto` 1, `wall.post` **0**. The same Submission/GUID was
used throughout; the proven pre-wall failure was explicitly repaired locally
through the normal start API after the code fix, without resetting its upload
attempt counter. No further live write was attempted after the three-attempt
limit. Writes=false and allowlist empty were restored after each isolated runtime
override; the API and environment stayed disabled throughout. The real
521-community/514-available grid was not started or sent. Investigate the live
pre-wall failure before enabling any production campaign.
