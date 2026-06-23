# Security Policy

The Nojoin team and community take all security vulnerabilities seriously. Thank you for your efforts to improve the security of Nojoin. We appreciate your efforts and responsible disclosure and will make every effort to acknowledge your contributions.

## Active Development

Nojoin is still in active development and all releases should be considered pre-release. There may be security vulnerabilities in the application. Nojoin's maintainers are not responsible for any data loss or security breaches that may occur as a result of using the application. We also advise users to take additional security measures in general but especially when deploying Nojoin over a publically accessible URL. For example, we recommend using a VPN or a reverse proxy to secure your Nojoin instance.

## First-Run Bootstrap Protection

Nojoin requires an operator-defined `FIRST_RUN_PASSWORD` before the first successful system initialisation can occur.

- The bootstrap password is only used while the system is uninitialised.
- The web client sends the bootstrap secret via the standard `Authorization` header using the `Bootstrap` scheme rather than a bespoke setup header.
- If `FIRST_RUN_PASSWORD` is missing, first-run setup fails closed until the operator sets it and restarts or redeploys.
- After initialisation, normal authenticated admin operations do not use the bootstrap password path.
- The bootstrap password is never returned by the API or persisted into Nojoin configuration.
- Application log output redacts `Authorization`, cookies, bootstrap credentials, passwords, tokens, and API-key fields if they are accidentally included in a log record.

Operators should treat `FIRST_RUN_PASSWORD` as a secret and ensure reverse proxies, ingress layers, and HTTP logging do not record `Authorization` headers or setup request bodies.
Nojoin also warns operators when `FIRST_RUN_PASSWORD`, `DATA_ENCRYPTION_KEY`,
or the tracked Redis/PostgreSQL placeholder secrets still match the shipped
deployment-template values. Those warnings appear in API or worker startup logs
and in the authenticated frontend, but they are advisory only and do not block
startup or first-run setup.

## Browser Session Request Protection

Nojoin's normal browser session uses a Secure HttpOnly cookie, but state-changing browser requests are not trusted solely because that cookie is present.

- Cookie-authenticated browser requests using unsafe methods must come from the trusted Nojoin web origin.
- The backend validates the standard `Origin` header, or falls back to `Referer` when needed, for those cookie-authenticated unsafe requests.
- Explicit bearer-token API clients are not subject to that browser-origin check.
- The session cookie remains `SameSite=Lax` to preserve expected top-level redirect flows such as OAuth callbacks, so unsafe GET-style side effects should continue to be avoided.

## Standard JWT Containment

Standard browser session and explicit API JWTs (token types `session` and `api`) support active invalidation in addition to natural expiry.

- Every issued session/API JWT carries a unique `jti` (token id), an `iat` (issued-at) timestamp, and a `tv` claim that mirrors the user's current `token_version`.
- Verification rejects a token whose `tv` no longer matches the user record. Bumping `token_version` therefore acts as an immediate kill-switch for every previously issued session and API token belonging to that user.
- `token_version` is bumped automatically when the user changes their own password, when an admin resets the password, and when an account is deactivated.
- Admins and Owners may also call `POST /api/v1/users/{user_id}/sessions/revoke-all` to forcibly invalidate every session for another user. Users themselves may call `POST /api/v1/users/me/sessions/revoke-all` to sign out of all other devices.
- Calling `POST /api/v1/login/logout` records the cookie token's `jti` in a server-side denylist (`revoked_jwts`), so the captured JWT stops verifying immediately even if a copy was made outside the browser cookie.
- The denylist also supports targeted revocation of an individual `jti`. Expired entries are pruned opportunistically.
Recording capture uses the normal browser session token. There is no separate desktop-helper JWT path for live recording.

## JWT Signing Key Rotation

JWT signing material is stored as a small keyring rather than a single static value.

- The keyring is persisted to `<user_data>/.secret_keys.json` and contains an `active` key id (`kid`) plus any prior keys that are still trusted.
- Every issued JWT carries a `kid` header. Verification picks the matching key from the keyring; if the `kid` is not known the token is rejected.
- Operators with shell access can rotate the key by calling `backend.core.security.rotate_signing_key()`. Rotation generates a fresh `kid`, makes it active, and (by default) keeps the previous key in the ring so currently outstanding tokens keep verifying until their natural expiry.
- After enough time has passed for outstanding tokens to expire, `prune_signing_keys()` removes retired keys from the keyring. Any token still signed by a removed key fails verification immediately, providing a hard cut-over.
- Setting the `SECRET_KEY` environment variable overrides the keyring with a single static key (intended for advanced deployments and tests). In that mode the rotation API is disabled.
- Existing single-key installs are migrated automatically: the legacy `<user_data>/.secret_key` file is loaded into the keyring as `kid="legacy"` on first startup and the legacy file is renamed.

## Browser Capture Security

Live recording is initiated and controlled by the authenticated web app.

- Browser recording endpoints use the normal Secure HttpOnly session cookie and enforce recording ownership on every init, segment, pause, resume, discard, and finalize request.
- Unsafe cookie-authenticated requests still require the trusted Nojoin web origin through the origin checks described above.
- Browser capture permissions are mediated by the browser. Nojoin cannot silently capture screen, tab, system audio, or microphone input without the user granting permission.
- The browser share picker determines the visible surface and whether shared audio is included on desktop. Nojoin warns when the browser does not grant a shared-audio track and then records microphone audio only. Mobile Chrome capture is microphone-only and does not expose tab, app, or system audio to Nojoin.
- A paused recording blocks new capture starts for that user until it is resumed or discarded, preventing overlapping segment streams.
- Refreshing, closing, or navigating away from the Nojoin tab moves the recording to `PAUSED`; uploaded segments remain server-side and only the current in-memory tail is dropped.
- Switching focus to another tab, window, or application does not pause recording.
- WebM/Opus, Ogg/Opus, and MP4 audio browser segments are transcoded in worker tasks before final WAV concatenation and final processing.
- Retired native-helper endpoints return structured `410 Gone` responses and do not issue credentials or accept uploads.

For end-user capture setup and troubleshooting, see [CAPTURE.md](CAPTURE.md).

## Supported Versions

As Nojoin is in active development, only the latest version is supported. We encourage all users to use the most up-to-date version of the application.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

Please report any security vulnerabilities privately. Do not open public GitHub issues or discuss vulnerabilities in public forums before they have been resolved.

### Private Reporting Channel

We use GitHub's Private Vulnerability Reporting feature to receive security disclosures securely and privately.

- **Submit a Report:** Visit [GitHub Private Vulnerability Report](https://github.com/Valtora/Nojoin/security/advisories/new) to submit your report.
- **Provide Details:** Please include a detailed description of the vulnerability, steps to reproduce, and any proof of concept (PoC) or exploit code.

### Expected Workflow

1. **Acknowledgment:** You will receive an initial response confirming receipt of your report within 48 hours.
2. **Evaluation:** The maintainers will investigate the report and determine the severity and scope of the vulnerability.
3. **Remediation:** If the vulnerability is confirmed, we will work on a patch. A security advisory will be drafted, and a fix will be released.
4. **Disclosure:** Once a patch is available and deployed, we will coordinate public disclosure through a GitHub Security Advisory.
