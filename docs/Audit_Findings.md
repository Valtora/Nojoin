# Audit Findings

This document captures the security audit items that remain open, plus findings that are only partially closed and still need follow-up work.

Status basis:

- Verified against the current codebase on 28 April 2026.
- This list excludes findings that appear closed in the current implementation.
- Items are written as implementation to-dos rather than as historical findings.

## Open To-Dos

- [ ] Introduce CSRF protection or tighten the current browser-session model further.
  - Current state: the session cookie remains `SameSite=Lax`, and normal browser authentication still accepts the cookie directly with no separate CSRF token mechanism.
  - Files to review: [backend/api/v1/endpoints/login.py](../backend/api/v1/endpoints/login.py), [backend/api/deps.py](../backend/api/deps.py)
  - Target outcome: state-changing browser requests should not rely only on `SameSite` plus CORS for cross-site request protection.

- [ ] Clean up temporary Companion WAV segment files after successful upload or terminal failure.
  - Current state: recording segments are written to the temp recordings directory and uploaded, but the file cleanup path is missing.
  - Files to review: [companion/src-tauri/src/audio.rs](../companion/src-tauri/src/audio.rs)
  - Target outcome: temp storage should not accumulate stale segment files across normal use.

- [ ] Replace timestamp-derived recording IDs with opaque identifiers.
  - Current state: recording IDs are derived from timestamps and remain enumerable.
  - Files to review: [backend/api/v1/endpoints/recordings.py](../backend/api/v1/endpoints/recordings.py)
  - Target outcome: recording identifiers should not leak creation timing or be guessable by sequence.

- [ ] Tighten the frontend Content Security Policy to remove inline script allowance.
  - Current state: the CSP still includes `script-src 'self' 'unsafe-inline'`.
  - Files to review: [frontend/next.config.ts](../frontend/next.config.ts)
  - Target outcome: migrate to a nonce-based or otherwise non-inline script policy.

## Partially Closed To-Dos

- [ ] Add token invalidation or rotation controls for standard browser and API JWTs.
  - Current state: the Companion flow improved substantially and now uses revocable pairing credentials plus encrypted local-control secrets, but standard session and API JWTs still rely on one persistent signing key and expire naturally rather than being actively revoked.
  - Files to review: [backend/core/security.py](../backend/core/security.py), [backend/api/v1/endpoints/login.py](../backend/api/v1/endpoints/login.py)
  - Target outcome: support explicit invalidation, key rotation, or another containment mechanism for leaked standard JWTs.

- [ ] Revisit throttling for the newer Companion auth endpoints.
  - Current state: the old unlimited long-lived companion token flow has been replaced, but the current Companion pairing, local-control token issuance, and credential exchange endpoints do not appear to be rate-limited.
  - Files to review: [backend/api/v1/endpoints/login.py](../backend/api/v1/endpoints/login.py)
  - Target outcome: protect the new Companion auth endpoints with targeted rate limits appropriate to their risk and expected usage.

- [ ] Review Companion logging for residual token or sensitive transport leakage and tighten log file handling.
  - Current state: the current logging paths do not obviously print bearer headers directly, but raw reqwest-style errors are still logged and the log file setup does not appear to add extra filesystem permission hardening.
  - Files to review: [companion/src-tauri/src/uploader.rs](../companion/src-tauri/src/uploader.rs), [companion/src-tauri/src/main.rs](../companion/src-tauri/src/main.rs)
  - Target outcome: confirm sensitive headers cannot leak through error formatting and ensure the log file permissions are acceptable for the deployment model.
