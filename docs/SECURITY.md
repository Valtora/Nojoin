# Security Policy

Nojoin is in active development, and every release should be treated as pre-release. The maintainers accept no responsibility for data loss or security breaches arising from its use. If you expose Nojoin on a public URL, put it behind a VPN or a reverse proxy you control.

## Supported Versions

Only the latest release receives security fixes.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

Report vulnerabilities privately. Do not open a public issue or discuss a vulnerability in public before it is fixed.

### Private Reporting Channel

Use GitHub's [Private Vulnerability Reporting](https://github.com/Valtora/Nojoin/security/advisories/new). Include a description, steps to reproduce, and any proof of concept or exploit code.

### Expected Workflow

1. **Acknowledgement:** you get a first response within 48 hours.
2. **Evaluation:** the maintainers establish the severity and scope.
3. **Remediation:** a confirmed vulnerability gets a patch, a draft security advisory and a release.
4. **Disclosure:** once the fix is released, disclosure is coordinated through a GitHub Security Advisory that credits the reporter.

## Operator Rules

- Set `FIRST_RUN_PASSWORD` and `DATA_ENCRYPTION_KEY` to long random values before first start, and keep `DATA_ENCRYPTION_KEY` unchanged afterwards, since changing it makes the secrets it protects unreadable ([DEPLOYMENT.md](DEPLOYMENT.md#configure-env)).
- Make sure reverse proxies, ingress layers and HTTP logging do not record `Authorization` headers or setup request bodies.
- Set `WEB_APP_URL` to the exact public HTTPS origin, and configure the proxy and `NOJOIN_TRUSTED_PROXIES` as [DEPLOYMENT.md](DEPLOYMENT.md#reverse-proxy-requirements) describes. Without the trusted proxy list, every client behind the proxy shares one rate-limit bucket.
- Treat backup archives as secrets. They hold every password hash and the calendar credentials in decrypted form ([BACKUP_RESTORE.md](BACKUP_RESTORE.md)).
- Verify image signatures before deploying ([DEPLOYMENT.md](DEPLOYMENT.md#verifying-an-image-before-deploying)), and subscribe to the repository's releases and security advisories.

## First-Run Bootstrap Protection

First-run setup needs the operator's `FIRST_RUN_PASSWORD`, which the web client sends in the `Authorization` header with the `Bootstrap` scheme. If the variable is unset, setup stays closed until it is set and the stack is restarted.

- The password works only while the system is uninitialised. The wizard creates the owner account partway through and signs in, and the server refuses the bootstrap credential from then on.
- The setup surface does not reveal whether the system is initialised. The sign-in page never mentions setup, `/setup` shows the same password prompt in every state, and every anonymous denial returns the same `403` (`First-run setup access denied.`) whether the password is wrong, unset, or the system is already set up. The reason goes to the server log only, and while the system is uninitialised the API startup log points to `/setup`.
- Setup requests are rate limited per client address before any state-dependent check, so throttling is the same in every state.
- The password is never returned by the API or stored in Nojoin's configuration.
- Application logs redact `Authorization`, cookies, bootstrap credentials, passwords, tokens and API-key fields.

The api and worker startup logs, and the web app once you are signed in, warn when `FIRST_RUN_PASSWORD`, `DATA_ENCRYPTION_KEY`, `REDIS_PASSWORD` or `POSTGRES_PASSWORD` still holds the template placeholder. The warnings do not block startup or setup.

## Passwords, Sign-In and Rate Limits

Passwords are hashed with Argon2 and need at least eight characters, not all of them whitespace. Registration needs an invitation code. After an admin password reset, the API accepts only three requests from that user until they choose a new password: reading their own profile, changing the password and signing out.

These endpoints are rate limited per client address:

| Endpoint | Limit |
| --- | --- |
| Sign-in (session and API token combined) | 10 per 10 minutes |
| Registration | 10 per hour |
| Invitation check | 30 per 10 minutes |
| First-run setup | 60 per 10 minutes |
| OAuth client registration | 10 per 10 minutes |
| OAuth token | 60 per 10 minutes |
| Anonymous MCP requests | 30 per minute |
| Calendar webhooks, per provider | 300 per minute |

## Browser Session Request Protection

The browser session is a `Secure`, `HttpOnly`, `SameSite=Lax` cookie that lasts 12 hours. A cookie-authenticated request with an unsafe method (anything but `GET`, `HEAD` or `OPTIONS`) must carry an `Origin` header, or failing that a `Referer`, that matches the trusted web origin, `WEB_APP_URL` ([DEPLOYMENT.md](DEPLOYMENT.md#remote-access-and-trusted-public-origin)). Otherwise it is refused with `403`. Session sign-in is held to the same rule. Requests that present a bearer token are not origin-checked.

`SameSite=Lax` keeps top-level redirects such as OAuth callbacks working, so a `GET` request must never change state.

The API also refuses plain HTTP: a safe request is redirected to the HTTPS origin and an unsafe one gets `400`. It rejects a `Host` header naming anything other than the `WEB_APP_URL` host or a loopback address, answers CORS only for that origin and the local development origins, and serves its OpenAPI schema and docs only to signed-in users.

The bundled nginx sends `Strict-Transport-Security: max-age=31536000` on every HTTPS response. It omits `includeSubDomains`, so the policy covers Nojoin's hostname and not its sibling hosts, and browsers apply it only over a trusted certificate ([DEPLOYMENT.md](DEPLOYMENT.md#hsts-and-other-security-headers)). The web app sends its own `Content-Security-Policy`, with a per-request nonce for scripts and styles, plus `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` and `Permissions-Policy`. Besides Nojoin's own origin, the policy allows `data:` images, `blob:` media, WebSocket connections and, when the frontend was built with an absolute `NEXT_PUBLIC_API_URL`, the API origin. For `/api/`, `/mcp` and the OAuth discovery documents, nginx adds a `default-src 'none'` policy and the same headers, without `Permissions-Policy` on the MCP routes.

## Standard JWT Containment

Nojoin issues three kinds of JWT: browser sessions (12 hours), API tokens from `POST /api/v1/login/access-token` (1 hour) and MCP connector tokens (1 hour). Each carries a unique `jti`, an `iat`, and a `tv` claim copied from the user's `token_version`, and each can be cut off before it expires.

- A token whose `tv` no longer matches the user record is rejected. `token_version` is bumped when users change their own password, when an admin resets a password or deactivates an account, and by two endpoints:
  - `POST /api/v1/users/me/sessions/revoke-all` invalidates every session and API token of the caller, the current session included.
  - `POST /api/v1/users/{user_id}/sessions/revoke-all` does the same for another user. Admins and owners may call it, and only an owner may revoke an owner's sessions.
- Each bump also revokes all of the user's MCP connector grants ([MCP Connector Access](#mcp-connector-access)).
- `POST /api/v1/login/logout` adds the session's `jti` to a server-side denylist (`revoked_jwts`), so a copy of the cookie stops working at once.

## JWT Signing Key Rotation

The api signs every token with HS256 using a keyring at `<user_data>/.secret_keys.json` (`data/.secret_keys.json` in the default deployment). The keyring holds an active key id (`kid`) and any older keys still trusted. Each token names its `kid` in its header, and a token with an unknown `kid` is rejected. Verification also refuses any other algorithm (including `none` and asymmetric ones), a malformed header, an expired token and an `iat` in the future, with no clock-skew leeway.

- **Rotation.** With shell access, call `backend.core.security.rotate_signing_key()`. It makes a new active key and keeps the old ones, so tokens already issued keep verifying until they expire. Then `prune_signing_keys(keep_kids)` removes retired keys, and any token they signed stops verifying immediately.
- **Static key.** Setting `SECRET_KEY` replaces the keyring with one static key and disables rotation. Use at least 256 bits of randomness, for example `openssl rand -hex 32`, as RFC 7518 requires for HS256. A shorter key still works, but PyJWT logs an `InsecureKeyLengthWarning`. The compose template does not pass `SECRET_KEY` to the api, so add it to the api service's `environment` if you use it.

If the key file is empty, malformed, unreadable or a directory, the api still starts but logs an error that names the file and the fix. Until it is fixed, sign-in fails with a message pointing to the api log, and every token is rejected.

| Problem | Fix |
| --- | --- |
| Empty or malformed key file (for example a blank file left by a full disk) | Delete the named file and restart. A new key is generated and everyone has to sign in again. |
| Unreadable key file | Give the api read and write access to the data directory. |
| Key file is a directory, usually left by a Docker bind mount of a host file that did not exist | Remove the directory and that bind mount, then restart. |

## Encryption at Rest

Nojoin encrypts these secrets in the database with a key derived from `DATA_ENCRYPTION_KEY`:

- calendar provider OAuth client secrets
- users' calendar access and refresh tokens
- calendar push-notification channel secrets
- CLI OAuth subscription credentials

If `DATA_ENCRYPTION_KEY` is unset, Nojoin generates `data/.data_encryption_key` and uses that. Changing the key makes these secrets unreadable. MCP authorization codes and refresh tokens are stored only as SHA-256 hashes. Nojoin encrypts nothing else: the database, recordings and transcripts rely on the host's own storage security.

## MCP Connector Access

The MCP connector ([MCP.md](MCP.md)) runs its own OAuth 2.1 authorization server and issues `mcp` tokens.

- An `mcp` token is bound to the `/mcp` resource and works nowhere else; every general API route rejects it.
- Grants carry `mcp:read` and, if consented to since the write tools shipped, `mcp:write`. Any other scope, including the never-released `mcp:destroy`, is refused with `invalid_scope`. An older grant without `mcp:write` keeps working for reads, and the write tools tell the user to reconnect.
- `mcp:write` covers recoverable changes only. No tool deletes data permanently or changes voiceprints, and assistants cannot write AI notes directly. The strongest deletion is moving a recording to the bin. Transcript corrections and speaker names made through MCP are labelled as AI edits in the app. `regenerate_notes` and `analyse_meeting` spend the user's own AI provider quota, and their tool descriptions say so.
- Authorization codes are single-use, PKCE-bound (S256 only) and expire after 60 seconds. Refresh tokens last 180 days and rotate on every use. Reusing a rotated refresh token revokes the whole grant.
- Dynamic client registration is open, as the MCP specification requires, but rate limited. It accepts only public PKCE clients with HTTPS or loopback HTTP redirect URIs, and a registration grants nothing: every grant needs consent from a signed-in user on an origin-checked page.
- Users list and revoke grants under **Settings > Integrations > Connected apps**. Revoking a grant stops it refreshing, and its current access token expires within the hour.
- Every `token_version` bump ([above](#standard-jwt-containment)) cuts off all of the user's connections at once. The new version rejects their access tokens, and the same transaction revokes every grant, because a refresh token carries no `tv`. A refresh racing the bump either finishes first and has its new token revoked, or waits and is refused. Each client then needs a fresh consent.
- A user who must change their password before consenting is sent to `/settings/profile` and back to the consent page afterwards. The return target must resolve to `/oauth/authorize` on Nojoin's own origin, so a crafted link can at most open a consent screen.
- `MCP_ANONYMOUS_DISCOVERY` (default `true`) lets `/mcp` answer five JSON-RPC methods without a token (`initialize`, `notifications/initialized`, `ping`, `tools/list` and `tools/call`), for clients such as Codex Desktop that cannot start OAuth from a bare `401`. An anonymous `tools/call` gets an in-band authentication challenge and runs nothing. The only data disclosed is generic tool metadata, the same on every deployment and published in MCP.md. Any other request, malformed ones included, gets `401`. Set the variable to `false` to send `401` to every unauthenticated request.
- `MCP_ENABLED=false` removes the connector: `/mcp`, the OAuth endpoints and the discovery documents all return `404` ([DEPLOYMENT.md](DEPLOYMENT.md#configure-env)).

## CLI OAuth Subscription Access

Each user can route their AI inference through their own **Claude** (Pro/Max) or **ChatGPT** (Plus/Pro) subscription (the per-user **CLI OAuth** route, off until the user connects one). This is an accepted risk: it uses a consumer subscription contrary to the provider's terms (Anthropic's, or OpenAI's for ChatGPT and Codex).

- The subscription token is encrypted at rest, one row per user and provider. It is never stored in user settings, logged or returned by the API.
- Inference runs as a single-turn subprocess on the `worker-io` lane with tools off (the Claude Code CLI with no allowed tools, or `codex exec --sandbox read-only`), as uid 1000, under a wall-clock timeout. A prompt-injected transcript can therefore only produce text: it cannot write files, run a shell or make network calls. No OS sandbox is added, because the Agent SDK's sandbox governs tool use, which is already off, and its network lockdown would cut the CLI off from the provider.
- The subprocess environment is scrubbed so an install-wide key cannot take precedence over the user's subscription. For Claude, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and `CLAUDE_CODE_USE_*` are removed and only `CLAUDE_CODE_OAUTH_TOKEN` is set. For ChatGPT, `OPENAI_API_KEY` and `CODEX_API_KEY` are left out. Unit tests lock both.
- Any failure, an exhausted usage limit included, falls back to the server's primary and then secondary provider.
- Usage records hold only a daily per-user rollup of token and request counts and the latest rate-limit reading. Prompt and response content is never stored.
- Disconnecting under **Settings > Your AI** deletes the encrypted credential and the user's CLI working directory.
- Both CLIs are installed unpinned, so each image build takes the latest release. The build verifies them against the npm registry's signatures and provenance attestations and fails if either does not verify. It records the installed versions at `/usr/local/share/nojoin/cli-versions.json`, and the SBOM attestation carries them too.

## Browser Capture Security

- Capture endpoints authenticate with the session cookie or an API token, apply the origin check above to the cookie, and enforce recording ownership on every init, segment, pause, resume, discard and finalize request.
- The browser mediates capture permissions. Nojoin cannot capture the screen, a tab, system audio or the microphone unless the user grants it in the browser's picker.
- Each user can have one capture in progress or paused at a time. When a tab loses its own capture before any audio reached the server, Nojoin discards it through the same ownership-checked endpoint as a manual discard, so this can never touch another tab's, device's or user's recording.
- A capture still marked as uploading can be resumed from another tab or device only once the server has received nothing from it for two minutes (`capture_looks_stalled` in `backend/models/recording.py`), so a capture that is uploading cannot be taken over. Nothing pauses, finalizes or discards such a capture automatically; its owner chooses in the resume-or-discard dialog.
- Segments the server has not yet acknowledged are kept in the browser's IndexedDB storage for the Nojoin origin, unencrypted like any other site data, until they are acknowledged or the recording is discarded or finalized from that browser. Only the recording's owner can upload them, and clearing the site's data deletes them.
- When the session ends during a capture, because it expired, was signed out or was revoked, the capturing tab does not reload to the sign-in page. It keeps recording into that IndexedDB queue and asks for the password in a dialog, which signs in through the same `POST /api/v1/login/session` endpoint, rate limit and origin check as the sign-in page. Nothing uploads until the server accepts a new session for the account that started the recording, so a sign-in as another account, in the dialog or in another tab, never resumes it. The session lifetime does not change. What the page already showed stays on screen behind the dialog until then.
- Live notes not saved yet, and text left in the note box, are kept, where the browser allows, in its IndexedDB storage (`nojoin-notes`), unencrypted like any other site data, under the recording and the id of the account they belong to, with each note's id and write token. A write token is not a credential. It only lets the server recognise a retried write as the same one, and every notes request still needs a session for the recording's owner. A record is removed once its notes are saved or discarded, the server has confirmed each discard, the box is empty and it holds no note of a kind only a later version can send. A page writes its record only while its session is that account. Each time a page learns from the server (`/users/me`) which account is signed in, every other account's records are removed. Signing out removes them all, waiting at most 2 seconds for the browser before going to the sign-in page; a browser that answers later can keep them until the next such removal. Where the browser has BroadcastChannel, a tab tells the browser's other open tabs of both, and each repeats the removal after any write of its own: a sign-out stops every tab storing notes until a sign-in as its account is accepted in it or in another tab, and a removal stops a tab storing for the removed account until that account is signed in again in this browser. Without it, another open tab goes on storing its own unsaved notes until it learns of the change from the server itself. A tab that has stopped storing keeps its notes in memory only. Each page load reads back the signed-in account's records once and sends their notes, whether or not their recordings are opened; text left in the box is restored but sent only from its recording's page. A record whose recording the server reports as not found, under a session `/users/me` confirms is that record's account, is shown in full and removed once the user copies or dismisses it. Two tabs on one recording share its record and the last to write wins, so a tab opened while another is live on the recording can restore that tab's half-typed text. The stored copy only covers a crash, a closed tab or an ended session: while anything is unsent, the browser asks before the page closes, stored or not, where it supports that; where it doesn't (iOS Safari), the stored copy is what covers a closed tab. Clearing the site's data deletes them.
- A page holding unsaved notes, or text in the note box, holds sign-in in place as a capture does: an ended session opens the same dialog instead of reloading to the sign-in page, and the notes are sent only once the server accepts a new session for the account that wrote them. The dialog's sign-in counts only once `/users/me` names that account by id, so the same account under a new name counts and another account given its old name does not. What the page already showed stays on screen behind the dialog until then. Unlike a capture's dialog, this one also offers to leave for the sign-in page. The browser asks first, where it supports that, and signing in there as a different account removes the notes stored in this browser.
- Capture segments are accepted as FLAC, WAV, WebM, Ogg or MP4. A FLAC segment, which every current browser build sends, must decode as 16 kHz, two-channel, 16-bit audio or it is quarantined. The other formats come from tabs on an older build and are transcoded on the CPU lane before use.
- The retired Companion app's endpoints return `410 Gone` and issue no credentials.

For capture setup and troubleshooting, see [CAPTURE.md](CAPTURE.md).

## Calendar Webhooks

Google and Microsoft call the push endpoints (`/api/v1/calendar/webhooks/google` and `/api/v1/calendar/webhooks/microsoft`) without a user session. Each notification is authenticated by a per-subscription secret encrypted at rest (the Google channel token or the Microsoft `clientState`). The payload only signals a change: the handler queues an incremental sync that runs with the account's stored tokens. Unknown or mismatched notifications are ignored, and the response never reveals whether a subscription exists.

## Outbound Connections and Anonymous Telemetry

The server makes these outbound connections:

- **Release metadata:** the GitHub Releases API (`api.github.com`), falling back to the GHCR tag list (`ghcr.io`) and the `docs/VERSION` file on `raw.githubusercontent.com`, for the update status in Settings and the admin update notice. Results are cached for ten minutes.
- **Models:** the transcription, diarisation and embedding models, mostly from Hugging Face, when first-run setup or an administrator prepares them, or on first use.
- **AI providers:** only when a non-local provider or a CLI OAuth subscription is in use. Local Ollama keeps inference on your own network.
- **Calendar providers:** only when a user connects a Google or Microsoft account.
- **Anonymous telemetry:** a daily heartbeat (a random install ID and the version), and optional diagnostics every six hours, sent from `worker-io` to `telemetry.nojoin.co.uk`. Neither contains meeting content, names, hostnames, URLs, credentials or model names, and the collector stores nothing derived from the connection. `NOJOIN_TELEMETRY_ENABLED=false` or a network block stops both without affecting anything else. [TELEMETRY.md](TELEMETRY.md) lists every field and opt-out.

## Vulnerability Scanning and Severity Policy

`scripts/release.py` scans every image with [Trivy](https://github.com/aquasecurity/trivy), for vulnerabilities and embedded secrets, before anything is pushed to the registry.

- A CRITICAL or HIGH finding that has a fix available fails the release (`--severity CRITICAL,HIGH --ignore-unfixed`).
- Findings with no upstream fix do not block the release. Nojoin cannot act on them, and the CUDA/PyTorch worker base image carries many. They stay visible in the scan output.
- A fixed CRITICAL or HIGH finding can be accepted only by a short-lived, documented exception in [.github/trivyignore](../.github/trivyignore) that records the CVE id, the reason, an owner and a review-by date. The entry is removed once the fix lands, usually through a Dependabot base-image or dependency update.
- Trivy cannot inspect the Codex CLI in `worker-io`, a stripped native binary, so the release script looks the installed Codex and Claude Code versions up in the GitHub advisory database and fails on any advisory ([DEVELOPMENT.md](DEVELOPMENT.md#release-gates)).
- CI also runs an informational Trivy scan of the npm lockfiles and the pinned Python requirements in `requirements/`. It reports CRITICAL and HIGH findings without blocking merges.

[DEPLOYMENT.md](DEPLOYMENT.md#verifying-an-image-before-deploying) covers image signing, provenance and SBOM attestations, the non-root runtime check, and how to verify an image.

## Emergency Security Release and Artifact Revocation

### Expedited Release

An out-of-band security fix skips none of the release gates; only the schedule is compressed.

1. **Draft privately.** Develop the fix on a private branch or in a GitHub Security Advisory draft, so nothing is disclosed before an image exists.
2. **Bump and release.** Merge the `docs/VERSION` bump and run `scripts/release.py` as for a normal release: a green `CI gate` on every commit since the last release, the build, the vulnerability scan, the health and non-root smoke and the CLI advisory lookup on the release host, then verification and cosign signing in the release workflow.
3. **Publish notes.** Add a security section to the generated release notes giving the affected versions, the severity and the required operator action, for example "upgrade immediately".
4. **Advisory.** Publish or update the GitHub Security Advisory, request a CVE where appropriate, and credit the reporter.

### Artifact Revocation

If a published image is compromised or dangerously vulnerable, revoke it as well as releasing a fix, because deployments may still pull the old tag or digest.

1. **Stop the bleeding.** Cut the patched release so the rolling tags (`latest`, `major.minor`) move to it. Never leave `latest` on a known-bad image.
2. **Remove or mark the bad version.** Delete the version tag and its digest from the GitHub Container Registry, or, where deletion would break audit history, mark it deprecated in the advisory. A deleted digest fails to pull.
3. **Invalidate trust signals.** Signatures attach to digests, so list every bad digest in the advisory as untrusted, valid signature or not.
4. **Notify operators.** Give the bad digests, the exact fixed digest to move to and any data-safety steps in the advisory and the release notes.
5. **Rotate exposed secrets.** If signing material, deployment credentials or registry tokens could have been exposed, rotate them and record it in the advisory timeline.
