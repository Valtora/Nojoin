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

## Companion Pairing and Local API Security

The Nojoin Companion app requires a strict manual pairing workflow.

- The Companion exposes a short-lived local API only for authenticated requests.
- Anonymous discovery of the Companion via the loopback interface is explicitly blocked. The frontend cannot silently detect if the Companion is running.
- First-run orientation, pairing initiation, repair, Firefox support, low-frequency utilities, and disconnect remain native-owned. The browser can collect the current pairing code and show coarse state, but it cannot silently start pairing, execute repair, or disconnect the backend on its own.
- Pairing must be manually initiated by the user from within the Companion app through `Start Pairing` or `Generate New Pairing Code`, which generates a single-use, short-lived 8-character pairing code.
- Firefox support remains explicit opt-in. Users must run `Enable Firefox Support`, enable Firefox enterprise roots, restart Firefox, and use a fresh pairing code before Firefox-based local control will work reliably.
- During pairing, the Companion captures and pins the first backend TLS certificate it sees for that backend target.
- The backend returns a revocable companion credential and a short-lived local control secret during pairing. The backend stores only a hash of the companion credential.
- On Windows, the Companion stores those secrets in a DPAPI-protected sidecar file rather than in `config.json`.
- The browser never receives a reusable Companion bearer token. The Companion exchanges its stored credential for a short-lived backend access token when it needs to call the backend.
- Re-pairing to a different Nojoin backend replaces the previous trust relationship atomically.
- After pairing, all Companion-to-backend HTTPS traffic requires the pinned backend certificate. If the backend certificate changes, the Companion must be explicitly re-paired.
- Browser repair remains a native-only action. Other surfaces may route the user to `Open Settings to Repair`, but the actual `Repair Local Browser Connection` action runs inside Companion Settings.
- Explicitly disconnecting the current backend from Companion Settings clears the saved backend certificate pin and local secret bundle, then attempts a best-effort remote revoke before returning the app to a clean first-pair state.
- All requests to the Companion's local API require a short-lived local control token and strict Host validation (e.g. `127.0.0.1` or `localhost`).

Operators and users should be aware that switching between deployments requires an explicit re-pair. Legacy plaintext Companion trust state is intentionally dropped by the current security upgrade, so existing installations must pair again after updating.

For end-user install, pairing, repair, and browser setup steps, see [COMPANION.md](COMPANION.md).

## Supported Versions

As Nojoin is in active development, only the latest version is supported. We encourage all users to use the most up-to-date version of the application.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

Please report any security vulnerabilities.

You should expect a response within 48 hours. If the issue is confirmed, we will release a patch as soon as possible, depending on the complexity of the issue.
