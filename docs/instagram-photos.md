# Instagram profiles as a photo source

Operators list Instagram profiles on the **Instagram** page (links, `@name` or names,
one per line). No categories are needed: the bot decides which profiles fit which
community.

## How it works

- **Sync (reference worker, read-only).** Once per `INSTAGRAM_SYNC_HOURS` (24 h) each
  enabled profile is read: the first time its last `INSTAGRAM_POSTS_PER_PROFILE` (50)
  posts, afterwards only the newest 12. Two readers exist: the official Graph API
  *Business Discovery* (free; Business/Creator profiles only) and an Apify Instagram
  post scraper (`INSTAGRAM_APIFY_ACTOR`, default `apify~instagram-post-scraper`, paid per
  post; any public profile, no Instagram login). With both configured the official API
  is tried first and profiles it cannot read (personal ones) go to the scraper; with
  only `APIFY_API_TOKEN` every profile is scraped. Photos and carousel photos are kept, videos
  and reels are skipped. Each new photo is downloaded once from the Instagram CDN
  (`*.cdninstagram.com`, `*.fbcdn.net` only, public addresses, size limits, no
  redirects to other hosts), normalized like every other photo, analysed with CLIP and
  stored as a `MediaAsset` with provider `instagram`, the post link as source and
  license "Publication rights unverified". The profile keeps a centroid of its photos.
- **Choice per community (planner).** Using the community's own wall photos, the bot
  takes the 3 profiles whose centroid is closest, then only those photos that are at
  least 90 % as close to the wall as the wall photos are to each other (at most 24).
  They join the usual pool: ranking, "never repeat across campaigns", cooldown and
  the learned model treat them like any candidate. Alternatives reserve up to 6 places
  for them. Without wall references (or without CLIP) no Instagram photo is used.
- Statuses: `pending` → `syncing` → `ready`, or `error` with a code
  (`instagram_profile_unavailable`: unknown or private profile, or a personal one
  without the scraper;
  `instagram_token_invalid`; `instagram_permission_missing`; `instagram_rate_limited`,
  retried after about an hour; `instagram_no_photos`; `instagram_unavailable`).
  "Загрузить заново" schedules a new read; deleting a profile stops its photos being
  candidates (history of photos already proposed is kept).

Private profiles cannot be read at all. The scraper reads public pages outside
Instagram's API terms; Instagram may change or block it, so readings can fail now
and then.

## Connecting the scraper (any public profile)

Set `APIFY_API_TOKEN` in `.env.server` (an Apify account with credit; the same token
the Pinterest Apify backend uses) and run `./deploy/server/update.sh`. Nothing is
needed on the Instagram side.

## Connecting the official API (Business/Creator profiles, free)

1. Switch our own Instagram account to a professional (Business or Creator) account
   and link it to a Facebook Page.
2. On developers.facebook.com create an app (type Business) and add the Instagram
   product (Instagram API with Facebook Login). Our own use works with the app in
   development mode, with our account having a role in the app.
3. Get a token with `instagram_basic`, `instagram_manage_insights`,
   `pages_read_engagement` (and `pages_show_list`). Prefer a long-lived token, or a
   System User token from Business Manager, which does not expire; a short user token
   lasts hours, a long-lived one about 60 days.
4. Find the Instagram Business Account ID: `GET /me/accounts` gives the Page id, then
   `GET /{page-id}?fields=instagram_business_account`.
5. Put both into `/opt/dropgrid/.env.server`:

   ```
   INSTAGRAM_ACCESS_TOKEN=...
   INSTAGRAM_BUSINESS_ACCOUNT_ID=...
   ```

   and run `./deploy/server/update.sh`. The page stops showing "Instagram не
   подключён"; profiles are read within minutes.

The token never appears in logs, errors or API output. Rights to the photos stay with
their authors, as with Pinterest.
