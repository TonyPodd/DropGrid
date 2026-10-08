# VK integration

Research checked on 2026-10-07. Foundation commit `a433e1f` was verified on
remote `TonyPodd/DropGrid/main` before implementation.

## Confirmed API contract

Primary reference: [VKCOM/vk-api-schema](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/README.md), pinned commit
`333481bd082ad747d4873ef4a77f9247097eeef0` (commit date 2025-04-14). The current public schema
advertises API **5.199**; npm package version 5.199.99 is a schema release number,
not the `v` request value. DropGrid centralizes this in Settings `VK_API_VERSION`.
This is the current published schema, not a claim that every VK app has the same
method availability. No unofficial articles/forums were used as contract evidence.

The current developer portal pages at dev.vk.com (wall.post, wall.get, wall photo
upload) could not be retrieved from this environment, including alternate official
language URLs. Scope grants and community-specific behavior remain unconfirmed.

| Operation | Contract from official schema | DropGrid |
| --- | --- | --- |
| users.get | Omitting user_ids uses current user; user/group/service token categories appear in schema | Require user-token setup; return one positive user id and display name |
| groups.getById | group_id can be id/screen name; response is an object containing groups, not the old flat array | Resolve normalized reference; persist id/name/canonical domain |
| wall.post | user token; negative owner_id targets a community; from_group=0 posts as user; post_id publishes an existing suggested/scheduled post | Build negative owner_id plus ordered photo/audio and optional caption; write guarded |
| wall.get | user/service token; domain, count, offset and filter parameters; suggests filter exists | Read all/suggests using domain, one page at a time |
| photos.getWallUploadServer | user token, positive group_id; upload_url/album_id/user_id response | Get ephemeral server only inside guarded upload pipeline |
| photos.saveWallPhoto | user token; group_id/photo/server/hash input; array of photo objects | Save exactly one photo and return typed attachment |

Sources: [users methods](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/users/methods.json),
[groups methods](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/groups/methods.json),
[groups response](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/groups/responses.json),
[wall methods](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/wall/methods.json),
[wall filter/object types](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/wall/objects.json),
[photo methods](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/photos/methods.json),
[photo response/objects](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/photos/objects.json).

### Can wall.post create a suggestion in someone else's community?

**Not conclusively confirmed by this schema alone.** It documents wall posting and
publication of already suggested posts via post_id. There is no explicit
create-suggestion flag. Negative owner_id with from_group=0 is the candidate user
posting payload; whether it enters suggestions, publishes immediately or is rejected
depends on community settings, user role and app permissions. The pure builder's
name describes its intended use, not a guaranteed result. The diagnostic reports
placement as unverified and must use a test community where either outcome is
acceptable. Never use this unresolved behavior for campaigns.

### Attachments and audio

Wall attachments are comma-separated `photo{owner_id}_{id}` and
`audio{owner_id}_{id}`. Typed VKAttachment serializes them, optionally including a
provided access_key. [Photo objects](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/photos/objects.json) and
[audio objects](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/audio/objects.json) define the identifiers and access_key.
The optional attachment key suffix follows the VK attachment convention; this
specific live behavior has not been tested here. The parser accepts direct
vk.com/vk.ru audio URLs, short audio references, mobile/www hosts, negative owners,
and `audio?z=audio.../context`. Keys are preserved only if explicitly supplied.
No audio upload or invented access keys.

### Suggested reads / future publication monitoring

wall.get filter=suggests is in the official filter enum. Access to someone else's
suggestions is not guaranteed by that enum: VK may deny access, and only permitted
results can be used. Read methods return typed count/items; no automatic pagination,
scheduler or repeated community scanning. Future publication matching compares
owner_id and audio id in attachments/repost copy_history, not URLs. Published posts
would be searched in the normal wall list; suggestions and publication are distinct
states. Lack of a match on one page does not establish absence.

### Permissions and important failures

The public method schema specifies token categories, but does not enumerate a
complete OAuth scope/role/application entitlement matrix. Obtain only the necessary
user-granted wall/photo capabilities through official OAuth and verify them on a
single test account before enabling writes. Do not assume that a valid users.get
result permits posting, photo saving or viewing a foreign suggestion queue.

[Official error catalog](https://github.com/VKCOM/vk-api-schema/blob/333481bd082ad747d4873ef4a77f9247097eeef0/errors.json):

| Codes | Interpretation / local behavior |
| --- | --- |
| 5, 18, 27, 28 | Authorization/account failure; validation marks Account invalid |
| 6, 9, 29 | Per-second limit/flood/rate limit; stop call, no automatic retry |
| 7, 15, 20, 200, 201, 214 | Permission/app type/media/post access denied; no retry |
| 203; 15 on group/wall reads | Community access denied; typed unavailable error |
| 14, 17 | CAPTCHA/security validation; stop, no bypass or retry |
| 100 and unknown codes | Malformed/generic API failure; no retry |
| 10 | Documented internal server error; bounded retries on reads only |

HTTP/network errors are separate typed transport errors. Raw API error messages,
request_params, redirects and CAPTCHA metadata are discarded; local fixed messages,
method and numeric code are retained.

## Live experiment results

**Not run.** No authorized test token/allowlisted target was configured for this run.
MockTransport tests verify wire shape and local behavior; they do not prove that
VK accepts an attachment, grants a scope or creates a suggestion in a foreign group.
The user-token-versus-community-token upload experiment was not run. User-only
support for wall.post and both wall photo API methods is confirmed by the schema;
no community-token production path is implemented.

## Local Account token onboarding

Token acquisition stays external/manual. DropGrid does not automate VKHost,
Marusia, private authentication, CAPTCHA, OAuth or account sessions.

`AccountTokenCipher` uses authenticated
[Fernet encryption from cryptography](https://cryptography.io/en/latest/fernet/).
`APP_SECRET_KEY` must be a URL-safe base64-encoded 32-byte Fernet key. Stored values
use `fernet:v1:` followed by ciphertext; the authenticated payload includes the
Account UUID, preventing credential ciphertext from being copied between Accounts.
The existing `encrypted_access_token` column is sufficient; no migration was added.
There is no plaintext fallback. Missing/invalid keys do not prevent app startup,
but credential import/read fails with a fixed sanitized error. Changing the key
requires reimporting credentials; automatic key rotation is not implemented.

Normal API Account validation, Community resolution and capabilities use
`DBTokenProvider`: active Account, stored VK identity and encrypted credential are
required. Decryption occurs inside the provider and returns `SecretStr`. It never
falls back to `VK_TEST_ACCESS_TOKEN`. Legacy env-bound diagnostics retain
`DevelopmentTokenProvider` and `VK_TEST_ACCOUNT_ID` for compatibility.

The token import/clear endpoints accept only `APP_ENV=development`. DropGrid has
no application authentication: keep API and Web UI on loopback, do not expose this
credential onboarding to the VPS/public Internet. Password-style input is cleared
after success, failure or cancellation; saved tokens are never returned/rendered.
`token_configured` means a stored credential exists, not that it is still valid.
No token is stored in browser persistence or sent to analytics. Request-body tracing
must stay disabled. Environment files and runtime media remain ignored by git.

### Local setup (without printing a key or token)

Run once from the repository root. This sets a Fernet key directly in ignored
`backend/.env` only if its current value is empty, and preserves other settings:

```sh
cd backend
uv sync
uv run python - <<'PYKEY'
import os
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import dotenv_values
path = Path('.env')
if dotenv_values(path).get('APP_SECRET_KEY'):
    raise SystemExit('APP_SECRET_KEY already exists; keep the current key.')
lines = path.read_text().splitlines() if path.exists() else []
lines = [line for line in lines if not line.strip().startswith('APP_SECRET_KEY=')]
lines.append('APP_SECRET_KEY=' + Fernet.generate_key().decode())
path.write_text('\n'.join(lines) + '\n')
os.chmod(path, 0o600)
PYKEY
```

Keep that key locally; losing it makes ciphertext unreadable. For Compose, put the
same key in its ignored root `.env` or inject it privately, then recreate **only API**.
Never publish either environment file or run `docker compose config` into reports.
Choose the intended DB and media volume together; an Account and MediaAsset must
exist in the database used by the diagnostic.

For host development, set `DATABASE_URL`/`MEDIA_STORAGE_DIR` in `backend/.env` to
that local DB/storage, keep `VK_WRITE_ENABLED=false` and the allowlist empty, then:

```sh
# Terminal 1, backend/
PYTHONPATH=src BACKEND_HOST=127.0.0.1 uv run python -m dropgrid.api
# Terminal 2, frontend/
npm ci
npm run dev -- --host 127.0.0.1
```

Set `VITE_BACKEND_URL` in ignored `frontend/.env.local` if backend uses another
port. Open `/accounts`. If no Account metadata exists, create it once through the
existing local API `/docs` → `POST /api/v1/accounts` with `{"name":"Local VK"}`;
leave VK user ID unset to bind it during import. Click **Добавить/заменить VK token**,
paste the externally obtained user token into the password input, then
**Validate / Save**. No token should be pasted into chat or command arguments.
The API calls `users.get`, requires one user and enforces any existing VK user ID
before committing encrypted credentials, display name and active status. Failed
replacement preserves the previous credential. Disabled Accounts are not activated.

### Read-only capabilities API

- `PUT /api/v1/accounts/{id}/token`: body `{"access_token":"<local input>"}`;
  response: account_id, vk_user_id, name, valid. No credential in response.
- `DELETE /api/v1/accounts/{id}/token`: removes credential only; Account/history remain.
- `POST /api/v1/accounts/{id}/vk/capabilities`: body
  `{"community_id":242100737}`. Performs `users.get`, exact-target `groups.getById`,
  and one `wall.get` page. Returns token_valid, current_user_id,
  community_resolved, wall_read, is_admin/is_member (null when unavailable), a
  sanitized error and **write_capability=UNTESTED**. It never uploads/posts/joins.

## Write safety

VK_WRITE_ENABLED defaults false. Generic call, wall posting, upload server retrieval,
photo multipart upload and save all fail before HTTP when disabled. Unknown method
names fail closed, so generic call cannot bypass the guard using another write method.
Diagnostics additionally require a positive exact id in comma-separated
VK_TEST_ALLOWED_COMMUNITY_IDS; empty list/wildcards/negative ids are rejected.
The diagnostic checks both guards before token retrieval, file access or read preflight.
No public write endpoint, no worker hook, no grid loop exists.

Reads retry up to VK_MAX_ATTEMPTS (3 default, maximum 5), with injectable exponential
backoff 0.25/0.5 seconds and a 5-second delay cap. Only connection/timeouts, HTTP5xx
and VK10 qualify. Writes/uploads are single-attempt: a timeout may mean the effect
already occurred. Never automatically rerun an uncertain write. A subsequent manual
invocation is a new operation, not an exactly-once guarantee across processes.

Per-account LocalRateLimiter serializes request starts in one process, default
1-second interval. It does not coordinate multiple replicas or discover VK limits.
Structured API logs contain method, duration_ms, success, error_code and attempt.
HTTPX/httpcore URL logging is suppressed; raw errors/authorization/upload URLs are
never logged by this integration. Do not enable third-party request-body tracing.

## Photo upload flow

```text
local JPEG/PNG bytes/path (bounded size + MIME/signature validation)
→ photos.getWallUploadServer(group_id positive)
→ ephemeral HTTPS upload_url, multipart field photo
→ server/photo/hash
→ photos.saveWallPhoto(group_id, server, photo, hash)
→ typed photo attachment
```

The API method names and save parameters come from the schema. The complete flow
and multipart upload are cross-checked against the official
[VK Java SDK example](https://github.com/VKCOM/vk-java-sdk/blob/3be91e5f2ab52133897e67f4b53379ee180d865a/README.md)
and [official multipart field selection](https://github.com/VKCOM/vk-java-sdk/blob/3be91e5f2ab52133897e67f4b53379ee180d865a/sdk/src/main/java/com/vk/api/sdk/actions/Upload.java).
No SDK dependency is installed.

Upload URLs are short-lived capabilities, not MediaAsset/source URLs. A separate
HTTP session sends binary data without the API token, API cookies or auth headers;
HTTPS trusted VK upload host suffixes only, redirects/proxies disabled by default.
The trusted-host list is a local policy, not a full host list guaranteed by the schema;
unrecognized hosts fail closed. Default photo limit is 10 MiB (local conservative
policy), JPEG/PNG magic/MIME checks only, no full image decoding/transcoding.
No photo search, downloading or persisted upload URL.

## Diagnostic CLI

Configure the single account in an ignored backend/.env (or inherited environment).
For Docker, use `docker compose exec api` before the Python commands below.

```sh
python -m dropgrid.integrations.vk.diagnostics account
python -m dropgrid.integrations.vk.diagnostics community example
python -m dropgrid.integrations.vk.diagnostics wall example
python -m dropgrid.integrations.vk.diagnostics wall example --suggests
```

Only after read diagnostics succeed, explicitly set both write guard/allowlist for
one known test target, then invoke once:

```sh
python -m dropgrid.integrations.vk.diagnostics suggest \
  --community-id 123 --track https://vk.ru/audio1_2 \
  --image /path/to/test.png --caption "DropGrid integration test"
```

suggest repeats a single-account/single-community read preflight, uploads one image,
and calls wall.post once. CAPTCHA/security errors abort immediately. Printouts are
summaries of numeric ids/counts or sanitized typed failures, never raw responses.
`--token` is rejected without echoing argument values. After the experiment restore
VK_WRITE_ENABLED=false. Never assume the returned post_id is a published post. A zero identifier is
retained as an ambiguous receipt, not converted into a publication URL.

## API endpoints

- POST /api/v1/accounts/{id}/validate: updates identity/name/status. Authentication
  rejection returns 200 with valid=false and sanitized error, commits invalid state.
  Other failures return typed sanitized HTTP errors without changing identity.
  Disabled accounts remain disabled and cannot be validated implicitly.
- POST /api/v1/communities/{id}/resolve: body `{"account_id":"UUID"}`; updates id/name/
  canonical domain using that account. A canonical-domain collision returns 409,
  preserves both records and does not implicitly merge grids/history.

Without an imported active Account credential/key these return 503; they do not acquire tokens.
This foundation still has no DropGrid authentication: keep it local.

## Future work

Key rotation and official permission/app verification remain future work.
A campaign sender is outside this stage. Photo Engine v1 is implemented separately.

## Live suggested-post experiment

Date: 2026-10-07. Explicitly selected test target: `clubantohahyesos`,
VK group ID `242100737`, name «собачки». Posting user `615459987` was confirmed
non-admin (`is_admin=0`). Account and target read-only preflight succeeded through
`users.get` and `groups.getById`.

Membership before: not member. One official `groups.join(group_id=242100737)`
attempt was rejected with sanitized category `VKPermissionError`, VK error code
`15`. Membership after: not verified following rejection. No retry or alternative
join was attempted. The experiment stopped before media selection or upload.
Manual suggestion UI: `NOT_VERIFIED` due to the Computer Use limitation; the
latest experiment instructions explicitly waived that UI precondition.

Joined proactively because some communities may restrict suggestions to
subscribers; requirement for this target was not independently established.
In this run the proactive join was attempted but **not completed**.

- Audio source/reference IDs: not selected.
- `photos.getWallUploadServer`: `NOT_RUN`.
- Multipart upload: `NOT_RUN`.
- `photos.saveWallPhoto`: `NOT_RUN`.
- `wall.post`: **0 attempts**, not sent; post ID unavailable.
- Actual placement: `UNKNOWN`; no post was created by this experiment.
- Placement reads (`filter=all` / `filter=suggests`): not run after the join rejection.
- `post_type`, `owner_id`, `from_id`: unavailable.
- Audio matched / photo present: not checked.

Observed conclusion: `UNCONFIRMED`. The rejected membership setup prevented
verification of ordinary subscribed non-admin suggested posting through the
current official token/app. Error code 15 alone does not establish which account,
app, token, or community restriction caused the denial, and says nothing about
whether `wall.post` would create a suggestion. Investigate the official membership
permission restriction before a separately authorized next experiment; do not
start a sender or repeat this run automatically.

Cleanup verified `VK_WRITE_ENABLED=false` and an empty
`VK_TEST_ALLOWED_COMMUNITY_IDS`. Campaign sends and other community writes: 0.
No token, upload URL, cookies, or secret keys are included in this record.

## Minimal text-only wall.post experiment

Date: 2026-10-07. Explicitly authorized target: `clubantohahyesos`, group ID
`242100737`, name «собачки». Read-only preflight confirmed posting user
`615459987`, `is_admin=0`, `is_member=0`, exact target identity, and successful
`users.get`, `groups.getById`, and `wall.get(owner_id=-242100737)`.

Previous `groups.join`: code `15`, sanitized category `VKPermissionError`;
subcode unavailable in the retained local report. **No groups.join calls were
made in this run.** The account remained a non-member, with membership no longer
a prerequisite under the explicit isolation-experiment instructions.

No photo/audio attachments were used in this experiment.

The new explicit `diagnostics suggest-text --community-id <ID>` mode requires
writes enabled and an allowlist containing exactly that single target. It checks
target identity and rejects an admin account, performs a wall read, and submits
only `owner_id=-<ID>`, `from_group=0`, and `message=DropGrid integration test`.
It shares the client's single-attempt write behavior: no automatic retry on
rejection, transport failure, or timeout. It does not upload media or join groups.
The CLI receipt leaves placement unverified; placement reads are a separate step.

Live result:

- Allowlist temporarily contained only `242100737`.
- `wall.post`: **exactly 1 attempt**, rejected.
- Sanitized category: `VKPermissionError`; code `15`; numeric subcode `1134`.
- No post ID returned.
- Post-attempt `wall.get(owner_id=-242100737, filter=all)`: success, count 0,
  returned 0.
- Post-attempt `wall.get(owner_id=-242100737, filter=suggests)`: success, count 0,
  returned 0.
- Placement: **REJECTED**.
- Post object / `post_type` / `from_id`: unavailable.
- Membership requirement: **UNKNOWN**. The denial does not establish whether the
  cause is membership, target settings, app/token restrictions, or another policy.
- Suggested-post capability with this account/token/target: **UNCONFIRMED**.
  This single minimal request was rejected; it is not evidence that every official
  VK API account/app is unable to create suggestions.

Cleanup ran in `finally`: `VK_WRITE_ENABLED=false`,
`VK_TEST_ALLOWED_COMMUNITY_IDS=`. Campaign sends, membership calls, media writes,
and other-community writes: 0. No automatic retry, approval, or deletion followed.
Investigate the exact wall.post code 15 / subcode 1134 before separately authorizing
another experiment or starting sender work. No token or raw API response is stored
in this documentation.

## Controlled existing-MediaAsset diagnostic

`suggest-media` uses DBTokenProvider only. It requires development mode,
`VK_WRITE_ENABLED=true` and **exactly** `VK_TEST_ALLOWED_COMMUNITY_IDS=242100737`.
A different target or additional allowlisted IDs are rejected before DB, credential,
file or network access. Its only permitted target is «собачки» / clubantohahyesos.
Do not enable these flags until explicitly preparing one experiment.

The command loads one enabled MediaAsset, resolves its key through LocalMediaStorage,
checks bounded normalized JPEG bytes, SHA-256, dimensions and decode/pixel limits,
then checks current user against Account.vk_user_id, exact group identity, confirmed
non-admin role and a single normal wall page. Unknown admin role fails closed.
No membership changes, scans, downloads, campaign operations or VK writes occur in
read preflight.

Once explicitly enabled, with a real user-provided audio reference:

```sh
# backend/, using the same DB, APP_SECRET_KEY and media directory as the API
PYTHONPATH=src uv run python -m dropgrid.integrations.vk.diagnostics suggest-media \
  --account-id '<IMPORTED_ACCOUNT_UUID>' \
  --community-id 242100737 \
  --media-asset-id '<EXISTING_DOG_MEDIAASSET_UUID>' \
  --track '<REAL_USER_PROVIDED_VK_AUDIO_URL_OR_REFERENCE>' \
  --caption 'DropGrid integration test'
```

Upload server retrieval → multipart upload → save photo → wall.post each run once.
Payload is `owner_id=-242100737`, `from_group=0`, photo first, audio second,
optional caption plus unique diagnostic marker; attachment access keys are preserved.
No publish_date/post_id input, no retry of writes, no implicit re-run. Upload errors
abort before wall.post. Transport uncertainty is UNKNOWN, never a reason to retry.

After an accepted post, read one page each of all/suggests. Matching requires owner,
marker and both attachment identities; receipt ID is supplementary evidence only.
Report SUGGESTED/PUBLISHED/NOT_FOUND/REJECTED/UNKNOWN with safe counts/matches/errors.
NOT_FOUND means no match on these pages, not proof of global absence. An inaccessible
suggestion queue with no normal-wall match is UNKNOWN; conflicting placement evidence
is UNKNOWN. Raw responses, post contents, attachment keys and credentials are not printed.
A sanitized rejection including numeric subcode is retained. Code 15/subcode 1134
stops the experiment; there is no alternate token acquisition or method fallback.
After any live attempt restore `VK_WRITE_ENABLED=false` and clear the allowlist;
do not delete the Account credential or MediaAsset.

### Prepared local asset and A/B status (2026-10-08)

Selected existing dog image visually: Couleur / Pixabay 6082017, dog in a garden,
1280×853 normalized JPEG. In the isolated `dropgrid-photo-live` DB its MediaAsset is
`d5156b7e-ebac-4445-8eb7-900195049935`. The asset already exists in its media volume;
no new photo download was performed. This UUID is local runtime data, not a portable
fixture; another DB must use its own existing MediaAsset UUID.

Baseline Mini App token: wall.post **code 15 / subcode 1134**, app_type_permission_denied.
Imported DB token: **NOT_RUN**. No DB-stored credential was available, APP_SECRET_KEY
was unset, writes were false and allowlist empty. The legacy env token was not migrated
without explicit import authorization. A real user-provided audio reference is also
required before running the command. No live VK requests/writes were made in this stage.

Normal tests mock VK; PostgreSQL integration tests check encrypted persistence,
failed replacement rollback, providers, read capabilities and MediaAsset pipeline.
Frontend tests cover password import, clear-on-error/success/cancel and indicators.
