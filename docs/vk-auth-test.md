# Development VK token helper

This opt-in helper uses the official [VK Bridge](https://github.com/VKCOM/vk-bridge)
package. It is not production OAuth, credential storage, or an Accounts connection.
It requires a VK Mini App context; opening the HTTPS URL directly is insufficient.
No protected key or service key is needed. The app ID is public configuration.

```text
VK Mini App
→ DropGrid /dev/vk-auth
→ VKWebAppGetAuthToken (explicit click)
→ Copy token (explicit click)
→ paste manually into ignored backend .env
→ live diagnostics (separate, explicitly invoked operation)
```

## Local frontend through external HTTPS (macOS)

From the repository root, install dependencies and the tunnel client:

```sh
npm --prefix frontend ci
brew install cloudflared
```

In terminal 1, start a [Cloudflare Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/):

```sh
cloudflared tunnel --url http://127.0.0.1:5173
```

It prints an HTTPS URL such as `https://random-words.trycloudflare.com`.
Keep this terminal running. Initially the origin may be unavailable until Vite starts.
In terminal 2, from the repository root, substitute that exact hostname (no scheme
or path) into the command below:

```sh
cd frontend
__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=random-words.trycloudflare.com \
VITE_VK_APP_ID=54808974 \
VITE_ENABLE_VK_AUTH_HELPER=true \
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

The [Vite host allowlist](https://vite.dev/config/server-options.html#server-allowedhosts)
allows only that tunnel hostname; do not set `allowedHosts: true`. These values are
process-local, not committed. Alternatively put the two `VITE_` values into ignored
`frontend/.env.local` and remove that file after testing. No backend is needed for
this helper; expose only this frontend port. Quick Tunnel URLs change on restart,
so update both the hostname and the Mini App development URL when restarting.

In the settings of your existing VK Mini App, set its development/testing HTTPS
URL for the platform you will use (web/mobile) to:

```text
https://YOUR-TUNNEL-HOST/dev/vk-auth
```

Launch the Mini App from inside VK using its development/test mode and the account
allowed to test it. The helper must load inside VK, not in a separate browser tab.
Click **Получить тестовый VK token** and accept the requested permissions. The
request sends the configured app ID and exactly `wall,photos,groups`. The page
shows only the requested scopes actually granted in Bridge's response, never the
token itself. Click **Copy token**. Clipboard access requires HTTPS and browser/VK
permission; on failure, allow clipboard access and click again. There is no alternate
token extraction or OAuth fallback.

## Manual handoff and diagnostics

Paste the copied value using your editor into ignored `backend/.env`:

```ini
APP_ENV=development
VK_TEST_ACCOUNT_ID=<the single Account UUID used for this test>
VK_TEST_ACCESS_TOKEN=<paste from clipboard here>
VK_WRITE_ENABLED=false
```

Do not put the token in shell commands, URLs, frontend env, source files, fixtures,
screenshots, logs, or issue reports. Click **Clear token** (or reload) after copying.
Clearing the page does not erase the operating system clipboard or revoke the VK
token; replace the clipboard contents manually after pasting.

Only when you explicitly intend to run a live read diagnostic, in a separate terminal:

```sh
cd backend
.venv/bin/python -m dropgrid.integrations.vk.diagnostics account
```

This uses the existing development-only token provider. See
[VK integration](vk-integration.md#diagnostic-cli) for its account binding and other
diagnostics. The helper does not invoke diagnostics, contact the backend, call VK
API methods, or publish posts. Backend env configuration is a separate manual step;
no Account credential architecture changes are involved.

## Lifetime, errors, and disabling

Token data lives only in React state, with no browser persistence, URL inclusion,
logging, analytics, or automatic clipboard writes. Clear, reload, and navigation
away discard the helper state. A pending operation cleared or unmounted cannot
restore a token when its response eventually arrives.

Initialization and authorization have a 25-second client-side timeout. This stops
the waiting UI; it cannot cancel a native VK overlay or revoke an issued token.
Late authorization responses are ignored. There is no automatic retry. Authorization
timeout shows `VK authorization did not finish. Try again.` Other fixed messages
cover cancellation, unavailable Bridge, rejected authorization, unknown errors,
and clipboard failure; raw Bridge/error objects are never rendered or logged.

Stop Vite and the tunnel when finished, remove any local helper env overrides, and
start the frontend normally. Only the exact value `VITE_ENABLE_VK_AUTH_HELPER=true`
enables `/dev/vk-auth`. The default/example value is `false`: the route shows the
ordinary not-found page, and the default production build excludes the helper and
VK Bridge bundle. The flag is a build-time opt-in, not access control: never enable
it in a production deployment. No actual configuration values are in the env example.
