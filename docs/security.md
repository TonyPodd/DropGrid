# Security

This foundation is for local development. DropGrid authentication is intentionally
out of scope; do not expose its API on a public network. Compose publishes only
loopback ports. CORS is not an access-control/authentication mechanism.

## Credentials

- Access tokens are never logged. Runtime logging records only explicit safe
  messages; database errors/third-party HTTP exceptions are not serialized.
  Telegram request logging is suppressed because its URLs contain the bot token.
- Account responses use an explicit schema excluding encrypted_access_token.
  Account create/patch reject unrecognized fields, including token fields.
  Validation errors omit input values to avoid echoing rejected credentials.
  No token ingestion or encryption is implemented. Settings secrets use SecretStr;
  never dump their raw values to logs.
- `.env`, `.env.*` (except `.env.example`) are Git-ignored and excluded from Docker
  build context. Configure TELEGRAM_BOT_TOKEN only through local environment.
- Compose DB credentials are public development defaults. Replace them and secret
  delivery before production; never reuse them for public databases.

## Required before production

**TODO:** implement encrypted-at-rest VK token storage with AES-GCM and separate key
management or a secret manager, rotation, revocation and backup protection. A field
named encrypted_access_token does not itself provide encryption. It remains null
through this API until a secure credential service is introduced.

Add DropGrid authentication/authorization before exposing account or campaign data.
Use least-privilege OAuth permissions based on the official VK API and user-authorized
accounts. Do not request unnecessary scopes or acquire accounts automatically.

Enable GitHub secret scanning and push protection in repository settings when
available. This scaffold does not change remote repository settings. pre-commit
includes detect-private-key; that is not a substitute for full secret scanning.
Review diffs and CI configuration for credentials before pushing.

## Platform boundaries

No CAPTCHA bypass, anti-fraud evasion, fingerprint spoofing, residential proxy
rotation, account purchasing/creation or blocking circumvention is included.
Future integrations must use the official API, permitted accounts, documented
capabilities and normal rate limits. Respect platform errors and user consent.

## Data and tests

Submissions retain account/media references and timestamps for future audit history.
No raw tokens belong in API payloads, error_message or error_code. Integration test
fixtures TRUNCATE their database: use TEST_DATABASE_URL only with a dedicated test
DB. Health returns generic state and does not expose DB credentials/errors.
