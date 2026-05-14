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
- Companion JWTs are not in scope for this mechanism. They are already revocable through the Companion pairing record and per-pairing `secret_version` documented above.

## JWT Signing Key Rotation

JWT signing material is stored as a small keyring rather than a single static value.

- The keyring is persisted to `<user_data>/.secret_keys.json` and contains an `active` key id (`kid`) plus any prior keys that are still trusted.
- Every issued JWT carries a `kid` header. Verification picks the matching key from the keyring; if the `kid` is not known the token is rejected.
- Operators with shell access can rotate the key by calling `backend.core.security.rotate_signing_key()`. Rotation generates a fresh `kid`, makes it active, and (by default) keeps the previous key in the ring so currently outstanding tokens keep verifying until their natural expiry.
- After enough time has passed for outstanding tokens to expire, `prune_signing_keys()` removes retired keys from the keyring. Any token still signed by a removed key fails verification immediately, providing a hard cut-over.
- Setting the `SECRET_KEY` environment variable overrides the keyring with a single static key (intended for advanced deployments and tests). In that mode the rotation API is disabled.
- Existing single-key installs are migrated automatically: the legacy `<user_data>/.secret_key` file is loaded into the keyring as `kid="legacy"` on first startup and the legacy file is renamed.

## Companion Pairing and Local API Security

The Nojoin Companion app uses a strict browser-initiated but locally approved pairing workflow.

- The Companion exposes a short-lived local API only for authenticated requests.
- Pairing starts from the authenticated browser session in `Settings -> Companion App`, but the browser still cannot silently complete pairing, disconnect the backend, or mint reusable Companion credentials on its own.
- The backend creates a short-lived pairing request, signs the `nojoin://pair` link fields with an Ed25519 backend identity key, and includes the backend origin, expiry, request id, request secret, and replacement flag in that signed payload.
- The local Companion verifies the signed link, verifies that the embedded backend identity key id matches the supplied public key, validates the backend origin as a safe HTTPS target, and rejects expired or malformed requests before contacting the backend.
- Pairing completion always requires an OS-native approval prompt on the same device that is running the browser and Companion app.
- During pairing, the Companion captures and pins the first backend TLS certificate it sees for that backend target.
- The backend returns a revocable companion credential and a short-lived local control secret during pairing. The backend stores only a hash of the companion credential.
- On Windows, the Companion stores those secrets in a DPAPI-protected sidecar file rather than in `config.json`.
- The browser never receives a reusable Companion bearer token. The Companion exchanges its stored credential for a short-lived backend access token when it needs to call the backend.
- Re-pairing to a different Nojoin backend replaces the previous trust relationship atomically.
- After pairing, all Companion-to-backend HTTPS traffic requires the pinned backend certificate. If the backend certificate changes, the Companion must be explicitly re-paired.
- Explicitly disconnecting the current backend from Companion Settings clears the saved backend certificate pin and local secret bundle, then attempts a best-effort remote revoke before returning the app to a clean first-pair state.
- If browser-side local control becomes unavailable, recovery is automatic where possible and otherwise requires the user to relaunch Companion. The browser does not expose a privileged repair command.
- All requests to the Companion's local API require a short-lived local control token and strict Host validation (e.g. `127.0.0.1` or `localhost`).
- Outbound Companion-to-backend HTTPS calls are constrained against server-side request forgery (SSRF):
  - The signed pairing link can only target a validated `https://host[:port]` origin with no embedded credentials, paths, queries, fragments, or whitespace.
  - Each outbound request resolves the target host once via the system resolver and pins the resulting socket addresses into the `reqwest` client through `resolve_to_addrs`. This closes the DNS-rebinding window between validation and connection.

Operators and users should be aware that switching between deployments requires an explicit re-pair. Legacy plaintext Companion trust state is intentionally dropped by the current security upgrade, so existing installations must pair again after updating.

For end-user install, pairing, reconnect, and troubleshooting steps, see [COMPANION.md](COMPANION.md).

## Supported Versions

As Nojoin is in active development, only the latest version is supported. We encourage all users to use the most up-to-date version of the application.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

Please report any security vulnerabilities.

You should expect a response within 48 hours. If the issue is confirmed, we will release a patch as soon as possible, depending on the complexity of the issue.
