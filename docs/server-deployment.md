# Shared DropGrid server

Public origin: **https://45.147.177.46**. Both **Tony and Tima have full DropGrid
access**, per the final operator instruction. They authenticate once with HTTP
Basic Auth; review attribution comes from their authenticated username, never
from an arbitrary browser-supplied reviewer ID. Local development retains its
reviewer selector. Real validation batch v2:
`4b376ed5-7961-4160-b201-e02caf44bb90` (150 items, initially zero decisions).
The original `48169905-c99f-4192-8466-cc69db360778` is preserved, closed.

Retrieve credentials as the server owner:

```sh
ssh root@45.147.177.46 'cat /root/dropgrid-access.txt'
```

Passwords are stored only in that root-only 0600 file; Caddy contains hashes.
`/opt/dropgrid/.env.server` is private and ignored. APP_ENV is production. The
original APP_SECRET_KEY is retained for migrated encrypted Account credentials.
Both authenticated users may manually import/replace/validate tokens. Missing or
spoofed proxy credentials are rejected. Tokens never appear in responses or logs.

## Runtime and boundaries

Compose: `docker-compose.server.yml`, project `dropgrid`, source `/opt/dropgrid`.
Persistent paths:

- `/srv/dropgrid/postgres`: PostgreSQL 16 data.
- `/srv/dropgrid/media`: original MediaAsset keys, `references/`,
  `previews/pinterest/`, `models/clip-vision-int8.onnx`.
- `/srv/dropgrid/backups`: initial migration dump/manifest and rotated daily dumps.

Services: postgres, one API process, static frontend, read-only publication worker,
one reference/preparation/learning worker. Sender has an opt-in profile and is
**not started**. No public database/API/frontend ports are published. Existing
personal-backend Caddy is the sole web entry point; API/frontend use aliases
`dropgrid-api`/`dropgrid-frontend` on `personal-backend_backend`. Postgres uses a
separate private network and a generated password. Existing personal-backend and
VK callback sites remain intact. No unrelated container is recreated by updates.

Caddy strips incoming identity/key headers, authenticates, and sets its own
`X-DropGrid-Remote-User` plus a private shared `X-DropGrid-Proxy-Key`. The API
requires both when TRUSTED_PROXY_ENABLED=true; clients cannot impersonate reviewers
by changing an action body. The key is also required for requests from the shared
internal network. Caddy access logs are discarded and API access logging disabled.
Do not expose Caddy's admin endpoint, database, or API directly.

Conservative limits: API/reference worker 640 MiB each, worker 96 MiB, frontend
48 MiB, PostgreSQL 192 MiB, shared_buffers 64 MiB, 40 connections. A 2 GiB swapfile
provides OOM insurance. Media preparation/VK reads/download concurrency is one;
only one visual worker runs. Monitor concurrent API/worker inference and reduce
workload before raising limits on a 2 GiB host.

## TLS

Certbot 5.4+ obtains a publicly trusted IP SAN certificate with `--ip-address` and
`--preferred-profile shortlived`, using HTTP-01 webroot. See the official
[Let's Encrypt IP certificate instructions](https://letsencrypt.org/2026/03/11/shorter-certs-certbot).
No local CA or insecure fallback is used. Caddy's global `default_sni 45.147.177.46`
selects the manually loaded IP certificate for browsers/curl that omit SNI.

Certificate lineage: `/etc/letsencrypt/live/dropgrid-ip`. The deploy hook copies
certificate/key into the existing Caddy config volume's `dropgrid/tls/`, validates
and reloads Caddy. HTTP challenge files live under that volume's `dropgrid/acme/`;
the HTTP route excludes authentication. Existing Caddy sites and config are backed
up before modification. Private keys stay 0600.

`dropgrid-cert-renew.timer` checks every six hours with jitter. Its service also
checks at least 24 hours of certificate validity; failures appear in systemd/journal.
Unit files are in `deploy/server/systemd/`; install/update them with
`sudo ./deploy/server/install-systemd.sh`. Monitor timer failures; short-lived IP certificates need continuous renewal.

```sh
systemctl status dropgrid-cert-renew.timer
systemctl status dropgrid-cert-renew.service
/opt/dropgrid-certbot/bin/certbot certificates
/opt/dropgrid-certbot/bin/certbot renew --cert-name dropgrid-ip --dry-run
openssl x509 -in /etc/letsencrypt/live/dropgrid-ip/fullchain.pem -noout -dates -ext subjectAltName
journalctl -u dropgrid-cert-renew.service
```

## Updates and backup

```sh
cd /opt/dropgrid
./deploy/server/update.sh
./deploy/server/backup.sh
docker compose --env-file .env.server -f docker-compose.server.yml ps
docker stats --no-stream
df -h
free -h
docker compose --env-file .env.server -f docker-compose.server.yml logs --tail 100 api reference-study
docker logs --tail 100 personal-backend-caddy-1
```

Update pulls/builds, migrates, starts only the explicit safe service list and checks
API health. Never use `down -v`, prune persistent volumes, or re-run the 514-community
preparation to deploy. Backup timer `dropgrid-db-backup.timer` runs daily; dated dumps
rotate after seven days, migration backup is retained separately. Dumps contain
encrypted credentials and must stay private.

Do not duplicate all media daily on this small disk. The frozen local DB/media
volume and `/Users/tony/.codex/backups/dropgrid/2026-10-10` remain initial off-server
backups. Periodically copy media and daily dumps to a private off-server directory:

```sh
mkdir -p "$HOME/dropgrid-backups/media" "$HOME/dropgrid-backups/database"
rsync -a --partial root@45.147.177.46:/srv/dropgrid/media/ "$HOME/dropgrid-backups/media/"
rsync -a --partial root@45.147.177.46:/srv/dropgrid/backups/ "$HOME/dropgrid-backups/database/"
```

Preserve `/srv/dropgrid/backups/media-manifest.json`; refresh the checksum manifest
when new local media is materialized. Media keys/checksums must agree with DB rows.
Initial archive transfer uses resumable rsync and verifies both archive and every
extracted file. After verified extraction only the redundant **server transfer tar**
may be removed to save disk; retain the original local tar and frozen media volume.

## Sender remains disabled

VK_WRITE_ENABLED=false and allowlist empty are forced by server Compose. Selecting
the sender profile alone does not enable writes. A future sending step requires a
separate explicit authorization, scoped allowlist, review/preflight and an intentional
change to these overrides. Never include sender in the normal update service list.

## Image/review QA

The clipping bug was a CSS grid minimum-content overflow: a square image rendered
704 px tall inside a 476 px canvas despite object-fit:contain. Fixed zero-minimum
grid tracks and bounded intrinsic image dimensions preserve every frame; storage
normalization/content endpoints do not crop. Alternatives/fullscreen also contain.

Run reusable five-shape browser regression against local Vite using a disposable
Playwright install (API and images are intercepted fixtures; no real decisions):

```sh
npm install --prefix /tmp/dropgrid-browser-qa playwright
NODE_PATH=/tmp/dropgrid-browser-qa/node_modules node frontend/qa/review-images.cjs
```

Screenshots default to `/tmp/dropgrid-image-qa`. QA checks portrait, landscape,
square, very wide and very tall on desktop/mobile, including fullscreen and overflow.
Real batch QA never confirms; action-flow QA uses a clearly marked sacrificial batch.
