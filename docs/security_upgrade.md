# Nojoin Companion Security Upgrade Plan

This document defines a waterfall implementation plan for the Companion local API hardening project.

The plan is intentionally sequential.
Each phase should be completed, reviewed, and signed off before the next phase begins.

## Problem Summary

The current Companion design has four structural weaknesses:

- Sensitive localhost endpoints can be called without a Companion-specific authorization layer.
- Pairing can be completed from a browser context without first requiring an explicit local user action inside the Companion app.
- Host validation is missing, which leaves room for DNS rebinding style abuse even when origin checks exist.
- Switching the Companion from one Nojoin backend to another is not robust because backend trust state and machine-local settings are not clearly separated.

The upgrade must fix those weaknesses while preserving a simple operating model:

- one Companion installation is paired to one Nojoin backend at a time
- switching backends requires an explicit re-pair
- the user initiates pairing manually from the Companion app
- the frontend never anonymously probes loopback just to discover whether the Companion is running

## Security Objectives

- Prevent unauthenticated localhost callers from controlling recording lifecycle endpoints.
- Prevent silent or background re-pairing to an attacker-controlled backend.
- Remove anonymous frontend detection of a running Companion.
- Require pairing to be initiated manually from the Companion app.
- Support deliberate backend switching through explicit re-pairing rather than simultaneous multi-backend trust.
- Preserve machine-local Companion settings when switching backends, while replacing backend-specific trust state atomically.
- Keep the existing backend token split intact: bootstrap tokens for recording initialization and short-lived per-recording upload tokens for upload operations.
- Preserve a usable recovery path when the backend is unavailable during a local recording session.

## Non-Goals

- Supporting simultaneous pairing to multiple Nojoin deployments.
- Supporting anonymous loopback discovery of an unpaired or paired Companion.
- Supporting remote or LAN control of the Companion local server.
- Broadening Companion support beyond the current Windows target.
- Replacing the global Nojoin API authentication model outside the Companion upgrade scope.
- Designing a generalized third-party local API for non-Nojoin clients.

## Clarified Pairing and Detection Stance

The frontend should not have any unauthenticated way to detect that a Companion process is running on loopback.

That means:

- no anonymous discovery endpoint
- no anonymous status endpoint
- no anonymous polling for `Connect Companion` or `Download Companion` logic
- no frontend-triggered opening of Companion pairing mode

Pairing should only ever begin when the user explicitly chooses a pairing action from the Companion app.

Recommended policy:

- The Companion exposes `Pair with Nojoin` in its Settings window.
- Entering pairing mode opens a Companion window that displays a short-lived pairing code.
- The pairing code format is 8 characters presented as 4 characters, a dash, then 4 characters, for example `ABCD-EFGH`.
- The user manually enters that code into the frontend of the Nojoin deployment they want to pair with.
- The frontend may attempt the pairing request only after the user submits the displayed code.
- If the Companion is not running, not in pairing mode, or the code is invalid or expired, the pairing request fails closed.
- Pairing mode must be time-bounded and single-use.
- Re-pairing to a different backend replaces the existing backend association only after a fully validated new pairing succeeds.

## Target End State

The finished architecture should have the following properties:

- The Companion stores one active backend association at a time.
- Companion configuration is split into:
  - machine-local settings that persist across backend switches
  - backend-specific trust state that is replaced atomically on successful re-pair
- There is no anonymous steady-state local API surface for the frontend.
- The only unauthenticated local pairing path is the short-lived pairing submission route, and it accepts requests only while local pairing mode is active and the displayed code matches.
- All authenticated local routes enforce:
  - strict `Host` validation
  - strict origin matching against the currently paired backend origin
  - a short-lived local control token bound to the current backend association and user context
- Pairing is only completed during an explicit, short-lived Companion-initiated flow.
- The pairing code is single-use, expires quickly, and is invalid outside pairing mode.
- Switching between development and production requires an explicit re-pair and never happens because of background polling.
- Local settings such as audio device preferences, run-on-startup behavior, and other machine-specific options survive backend switching where that is safe.
- Re-pairing is blocked while a recording is active or uploading unless a separately approved emergency override is used.

## Settings Boundary

The design should explicitly separate the following state categories.

Machine-local settings to preserve across backend switches:

- local port
- audio device selections
- run-on-startup preference
- local UI or tray preferences
- any other setting that belongs to the machine rather than to a specific backend trust relationship

Backend-specific state to replace atomically on successful re-pair:

- backend origin
- API host and port
- TLS fingerprint
- local control secret or equivalent pairing material
- any stored bootstrap or pairing credential that should not survive a backend switch
- backend-facing status flags tied to the previous trust relationship

## Expected Code Touch Points

The implementation will likely touch at least the following areas:

- `backend/api/deps.py`
- `backend/api/v1/endpoints/login.py`
- `backend/api/v1/endpoints/recordings.py`
- `backend/core/security.py`
- a new backend companion pairing or local-control endpoint module
- `companion/src-tauri/src/config.rs`
- `companion/src-tauri/src/state.rs`
- `companion/src-tauri/src/server.rs`
- `companion/src-tauri/src/main.rs`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/serviceStatusStore.ts`
- `frontend/src/components/MainNav.tsx`
- `frontend/src/components/MeetingControls.tsx`
- `frontend/src/components/LiveAudioWaveform.tsx`
- `frontend/src/components/settings/SettingsPage.tsx`

## Waterfall Delivery Plan

## Phase 0 - Requirements Freeze and Threat Model

Goal: freeze the security and UX rules before code changes start.

Tasks:

- [x] 0.1 Review and approve the Phase 0 frozen spec in this document.
- [x] 0.2 Review and approve the no-anonymous-detection rule.
- [x] 0.3 Review and approve the manual Companion-initiated pairing flow.
- [x] 0.4 Review and approve the pairing code contract and expiry model.
- [x] 0.5 Review and approve the route classification and token contract.
- [x] 0.6 Review and approve the machine-local versus backend-specific settings boundary.
- [x] 0.7 Review and approve the active-recording guard and emergency override stance.
- [x] 0.8 Publish the approved Phase 0 spec as the implementation baseline for later phases.

Exit criteria:

- [x] Phase 0 frozen spec is approved.
- [x] Anonymous detection is explicitly rejected.
- [x] Pairing UX rules are approved.
- [x] Pairing code rules are approved.
- [x] Ownership and override rules are approved.
- [x] Settings-boundary rules are frozen.

### Phase 0 Frozen Specification

This section is normative.
Later phases should implement this contract unless this document is explicitly revised.

### 0.A Operating Model

- The Companion is paired to exactly one Nojoin backend at a time.
- Switching from one backend to another requires a full re-pair.
- Pairing mode can only be opened by an explicit local user action inside the Companion app.
- The frontend must not attempt to detect whether the Companion is running before the user initiates pairing.
- Outside pairing mode, the frontend should assume nothing about loopback availability.
- Re-pair must be blocked while a recording is active or uploading.
- A tray-level emergency stop override may exist for active recordings if later approved, but that override does not permit backend switching during the active session.

### 0.B Threat Model Summary

The design must assume all of the following are possible:

- An arbitrary web page can attempt to call localhost.
- A malicious page can attempt DNS rebinding or non-loopback `Host` tricks.
- A user may run both development and production deployments on one machine and may intentionally re-pair between them.
- An attacker may wait for the user to open pairing mode and then race a pairing attempt.
- A stale local token or stale backend trust block may remain after interrupted upgrades or failed re-pair attempts.

The design does not need to support:

- Simultaneous trusted control from multiple Nojoin backends.
- Anonymous discovery of a Companion process.
- General local API access for non-Nojoin clients.

### 0.C Local Route Classification

The local Companion API is divided into two route classes only.

Class 1: Pairing route

- Purpose: complete a short-lived manual pairing flow.
- Authentication model: valid active pairing mode plus valid pairing code plus valid backend bootstrap token.
- Availability: only while pairing mode is open.
- Allowed unauthenticated steady-state discovery: none.

Class 2: Authenticated steady-state routes

- Purpose: status, settings, devices, waveform, update triggers, and recording controls.
- Authentication model: short-lived local control token.
- Availability: only after a successful pairing exists.
- Anonymous access: never.

No third route class should exist.

### 0.D Required Route Behavior

The following route behavior is required.

Pairing completion route:

- Method: `POST`
- Recommended path: `/pair/complete`
- Request origin model: browser-origin request from the currently authenticated Nojoin frontend the user chose to pair with.
- `Host` validation: always required.
- CORS model: while pairing mode is open, the Companion may reflect the requesting origin for this route only.
- Availability: only while pairing mode is active.
- Success effect: atomically replace the current backend-specific trust block.

Authenticated steady-state routes:

- Existing local control and read routes such as status, config, devices, live levels, start, stop, pause, resume, and update must all require local control authentication after this upgrade.
- CORS must allow only the exact currently paired frontend origin.
- Requests with no valid local control token must fail closed.

### 0.E Pairing Code Contract

Pairing code requirements:

- Display format: `ABCD-EFGH`
- Stored canonical format: `ABCDEFGH`
- Character set: uppercase alphanumeric characters excluding ambiguous values `0`, `1`, `I`, `L`, `O`, and `U`
- Case handling: frontend input is normalized to uppercase before submission
- Lifetime: 5 minutes from generation
- Use count: single successful use only
- Failure budget: 5 invalid submissions per pairing window
- Expiry behavior: once expired, the code is unusable and pairing mode closes
- Replay behavior: once a pairing succeeds, the code is invalid permanently
- Regeneration: opening a new pairing window generates a new independent code

Pairing code presentation requirements:

- The Companion pairing window must show the formatted code and a visible countdown.
- The pairing window must clearly tell the user to enter the code into Nojoin.
- Closing the pairing window immediately cancels the code.

### 0.F Pairing Completion Request Contract

The frontend-to-Companion pairing completion request should carry the following payload.

Required body fields:

- `pairing_code`
- `bootstrap_token`
- `api_protocol`
- `api_host`
- `api_port`

Optional body fields:

- `tls_fingerprint`

Implicit data taken from the request:

- `Origin` header becomes the paired web origin after validation succeeds.

Validation rules:

- Pairing mode must be active.
- The submitted pairing code must match the currently displayed code.
- The code must be unexpired and unused.
- The request `Host` must resolve to an approved loopback form.
- The backend bootstrap token must validate successfully.
- If provided, the TLS fingerprint must be persisted as part of the new backend trust block.
- If a recording is active or uploading, the request must be rejected.

Recommended response codes:

- `200`: pairing succeeded
- `400`: malformed request payload
- `401`: invalid backend bootstrap token
- `403`: pairing mode not active or code mismatch
- `409`: pairing blocked because recording or upload is active
- `410`: pairing code expired or already used
- `429`: too many invalid code submissions

### 0.G Machine-Local Versus Backend-Specific State

Machine-local state must survive backend switching.

Machine-local state includes:

- local server port
- selected input device
- selected output device
- run-on-startup behavior
- local tray and settings preferences that are not trust-related

Backend-specific state must be replaced atomically on successful re-pair.

Backend-specific state includes:

- paired web origin
- backend API protocol
- backend API host
- backend API port
- TLS fingerprint
- local control secret material
- any cached or persisted trust material tied to the previous backend

Atomicity rule:

- A successful re-pair must replace the full backend-specific state block in one commit.
- A failed re-pair must leave the previous backend-specific state untouched.

### 0.H Local Control Token Contract

All steady-state local routes must require a short-lived local control token after pairing.

Token transport:

- Use the `Authorization` header with the `Bearer` scheme for local control tokens.

Required claims:

- `aud`: fixed local Companion audience value
- `sub`: authenticated backend subject
- `user_id`
- `username`
- `origin`: exact paired web origin
- `actions`: allowed local action scopes
- `iat`
- `exp`
- `secret_version` or equivalent rotation marker

Binding rules:

- The token must be signed with the current backend-specific local control secret or equivalent per-pairing material.
- The Companion must reject tokens whose origin claim does not exactly match the currently paired web origin.
- The Companion must reject tokens signed with stale secret material after re-pair or rotation.

Recommended action scopes:

- `status:read`
- `settings:read`
- `settings:write`
- `devices:read`
- `waveform:read`
- `recording:start`
- `recording:stop`
- `recording:pause`
- `recording:resume`
- `update:trigger`

### 0.I Host and Origin Validation Rules

Host validation rules:

- Every local route must validate `Host`.
- Accept only loopback forms such as `127.0.0.1`, `localhost`, and any explicitly supported IPv6 loopback equivalents if added later.
- Reject all non-loopback hostnames even if the socket is bound to loopback.

Origin validation rules:

- Authenticated steady-state routes must allow only the exact currently paired web origin.
- The pairing completion route may temporarily allow the requesting origin while pairing mode is open.
- No hard-coded blanket allowance for `localhost:3000` or similar development origins should remain after the upgrade.

### 0.J Recording Guard Rules

Recording guard requirements:

- `start`, `stop`, `pause`, and `resume` require a valid local control token.
- `start` also requires a valid backend bootstrap token as it does today.
- The implementation must persist active recording ownership metadata at recording start.
- `pause`, `resume`, and `stop` should default to same-user enforcement unless a later approved override rule is introduced.
- Re-pair attempts must return `409` while a recording is active or uploading.

### 0.K Frontend UX Contract

Frontend requirements:

- The frontend must not poll or probe localhost anonymously.
- The frontend may show a static `Pair Companion` or equivalent entry point without first checking loopback.
- Starting pairing from the frontend is not allowed.
- The frontend pairing UI should instruct the user to open the Companion app, choose pairing there, and enter the displayed code.
- The frontend should submit pairing only after the user enters the displayed code manually.
- The frontend may begin authenticated local status polling only after pairing succeeds.

### 0.L Documentation Contract

The following documentation updates are mandatory once implementation is complete:

- `ARCHITECTURE.md` must describe the one-backend pairing model and the manual code flow.
- `PRD.md` must stop implying anonymous Companion detection or frontend-initiated pairing.
- `USAGE.md` must describe manual pairing and re-pairing when switching between backends.
- `GETTING_STARTED.md` must describe the new first-pair flow.
- `ADMIN.md` must describe the operational consequences of re-pairing and backend switching.
- `DEVELOPMENT.md` must describe how developers switch a single Companion between dev and prod by re-pairing.
- `SECURITY.md` must describe the removal of anonymous loopback detection and the manual pairing safeguard.
- `AGENTS.md` must be updated so future implementation work follows this model.

### 0.M Phase 0 Deliverable

The deliverable of Phase 0 is an approved frozen specification.

Implementation phases that follow should treat the following as already decided unless this document is revised:

- no anonymous Companion detection
- manual Companion-initiated pairing only
- `ABCD-EFGH` pairing code format
- one Companion to one backend at a time
- atomic replacement of backend-specific trust state on re-pair
- authenticated steady-state local API only

## Phase 1 - Companion Connection State Model and Config Migration

Goal: separate machine-local settings from backend-specific trust state and make backend switching atomic.

Tasks:

- [x] 1.1 Inventory every currently persisted Companion field and classify it as machine-local, backend-specific, derived runtime state, or legacy-only migration input.
- [x] 1.2 Freeze the exact field classification for `local_port`, `input_device_name`, `output_device_name`, `run_on_startup`, `min_meeting_length`, `last_version`, `api_protocol`, `api_host`, `api_port`, `api_token`, `tls_fingerprint`, and paired web origin.
- [x] 1.3 Introduce an explicit persisted config root with a schema version so future migrations do not depend on shape inference alone.
- [x] 1.4 Introduce a machine-local settings block for fields that must survive backend switching.
- [x] 1.5 Introduce a backend connection block for all trust-coupled fields, including the paired web origin and placeholders for future local-control secret material.
- [x] 1.6 Add config helper accessors for API URL, paired web origin, TLS fingerprint, and paired/authenticated state so call sites stop reaching into raw fields directly.
- [x] 1.7 Add one backend-replacement API on the config layer that swaps the entire backend connection block in one write.
- [x] 1.8 Add a machine-local update API on the config layer that updates device and local settings without mutating backend trust state.
- [x] 1.9 Move the currently runtime-only paired web origin into persisted backend-specific state and define how the runtime cache derives from it.
- [x] 1.10 Refactor Companion call sites in `main.rs`, `server.rs`, `audio.rs`, and `uploader.rs` to use the new config helpers instead of top-level backend fields.
- [x] 1.11 Remove field-by-field backend mutation from pairing, recording start, and settings update flows.
- [x] 1.12 Add migration from the oldest legacy config shape (`api_url`, `web_app_url`, flat device fields) into the new root structure.
- [x] 1.13 Add migration from the current flat config shape into the new root structure without losing machine-local preferences.
- [x] 1.14 Define malformed-config recovery rules so unsafe backend trust state is cleared or rebuilt without mixing partially migrated data into a live pairing.
- [x] 1.15 Preserve save-path behavior, dev override loading, and platform-specific app-data paths during the schema change.
- [x] 1.16 Add round-trip serialization tests for the new config format.
- [x] 1.17 Add migration tests for both legacy and current flat config inputs.
- [x] 1.18 Add atomic replacement tests proving that failed re-pair preparation cannot leave a half-old, half-new backend block on disk.
- [x] 1.19 Add regression tests proving that machine-local settings survive backend replacement unchanged.

Exit criteria:

- [x] Every persisted Companion field has an approved classification.
- [x] The config file has a versioned root with separate machine-local and backend-specific blocks.
- [x] Paired web origin and trust-coupled fields live only inside the backend connection block.
- [x] Backend replacement happens through one atomic config operation rather than field-by-field mutation.
- [x] Legacy and current flat configs migrate successfully into the new structure.
- [x] Machine-local settings are preserved cleanly across backend switches in tests.
- [x] Companion call sites no longer depend on top-level `api_*`, `api_token`, and `tls_fingerprint` fields.

## Phase 2 - Backend Pairing Contract and Persistence

Goal: support secure one-backend pairing and re-pair without introducing multi-deployment profile logic.

Tasks:

- [x] 2.1 Add backend endpoints for the manual code-based pairing workflow.
- [x] 2.2 Define the authenticated frontend request that turns a user-entered pairing code into a pairing payload.
- [x] 2.3 Persist backend-side local control secret material or equivalent pairing state needed for later local control token issuance.
- [x] 2.4 Define overwrite semantics when a Companion previously paired elsewhere is re-paired to this backend.
- [x] 2.5 Define revocation and secret rotation rules for re-pair and manual unpair flows.
- [x] 2.6 Fail closed when the backend sees stale, conflicting, or partially rotated pairing state.
- [x] 2.7 Add tests for first pair, re-pair, revoked pair, rotated secret, and incomplete cleanup cases.

Exit criteria:

- [x] The backend supports secure first-pair and re-pair flows.
- [x] No multi-deployment profile model is required to switch backends.

## Phase 3 - Local API Guard Primitives

Goal: implement the technical guardrails used by every local route.

Tasks:

- [x] 3.1 Add strict `Host` validation for every local route.
- [x] 3.2 Canonicalize accepted loopback hosts such as `127.0.0.1`, `localhost`, and future explicit IPv6 loopback support if added.
- [x] 3.3 Reject any request whose `Host` does not resolve to an approved loopback form.
- [x] 3.4 Add authenticated route guards that require a short-lived local control token for all steady-state local routes.
- [x] 3.5 Bind origin checks to the currently paired backend origin instead of broad hard-coded development origins.
- [x] 3.6 Return consistent `401`, `403`, and `409` responses for unauthenticated, unauthorized, expired-pairing, and busy-state cases.
- [x] 3.7 Add tests for malformed `Host`, rebinding-style hostnames, missing tokens, expired tokens, and wrong-origin tokens.

Exit criteria:

- [x] There is one reusable local request guard model.
- [x] Sensitive routes can no longer be called without local control auth.

## Re-Baselined Delivery Plan (2026-04-22)

The original phase order no longer reflects the actual dependency chain in the codebase.

Completed in full:

- Phase 0
- Phase 1
- Phase 2
- Phase 3

Delivered ahead of the original schedule and now treated as completed foundation work:

- the manual Companion-initiated pairing flow, including the pairing window, displayed short-lived code, and pairing-route enforcement
- local control token issuance and Companion-side validation
- removal of anonymous localhost detection from the frontend
- authenticated frontend calls for steady-state local Companion routes
- local-control auth on recording lifecycle routes
- Companion paired-state display, manual unpair action, and blocked-state copy

The remaining work is re-grouped below into the next sequential waterfall plan.
Old task numbers are referenced in parentheses for traceability.

## Phase 4 - Re-Pair Completion and Pairing State Closure

Goal: finish the backend-switch mechanics so pairing and re-pairing can be treated as final and safe.

Tasks:

- [x] 4.1 Complete atomic replacement of backend trust state on successful pairing and clear stale secrets from the previously paired backend. (was 4.10)
- [x] 4.2 Reject backend-switch or re-pair attempts while recording or uploading is active across the full pairing path. (was 6.6)
- [x] 4.3 Add Settings-window controls for entering and cancelling pairing mode. (was 8.1)
- [x] 4.4 Add regression tests for Settings-driven pairing flow, invalid code, expired code, replay, closed pairing window, and blocked re-pair during active recording. (was 4.11)

Exit criteria:

- [x] Successful re-pair replaces the backend trust block atomically and leaves no stale secrets behind.
- [x] Re-pair is impossible while recording or upload work is active.
- [x] Pairing mode can be entered and cancelled locally from the Settings window.
- [x] Pairing-state transitions have regression coverage.

## Phase 5 - Recording Ownership and Offline Recovery

Goal: finish the user-level authorization model for active recordings and define the safe local recovery path.

Tasks:

- [x] 5.1 Decide whether `pause`, `resume`, and `stop` must also carry a fresh backend-derived identity token or whether stored recording-owner metadata is sufficient. Decision: stored recording-owner metadata plus the existing short-lived local-control token is sufficient; `start` remains the route that also requires a fresh backend bootstrap token. (was 6.3)
- [x] 5.2 Enforce same-user or approved-override rules for `pause`, `resume`, and `stop`. Current implementation enforces same-user by default using stored recording-owner metadata plus the validated local-control token claims. (was 6.5)
- [x] 5.3 Add a tray-level emergency stop policy for offline or expired-token recovery. Current implementation keeps an active recording running locally when backend health checks fail, notifies the user that they may need to stop it from the tray, and queues saved audio for upload once Nojoin reconnects if they stop while offline. (was 6.7)
- [x] 5.4 Add tests for owner mismatch, cross-tab collisions, blocked backend switch during active recording, and backend-offline stop behavior. Owner mismatch and same-user cross-tab checks live in `server.rs`, blocked backend switching during an active recording was covered in Phase 4, and backend-loss state tracking plus offline manual-stop upload queue behavior now have direct Companion regression tests. (was 6.8)

Exit criteria:

- [x] Recording lifecycle actions are bound to both the paired backend and the allowed local caller.
- [x] There is a defined and tested recovery path when the backend is unavailable.

## Phase 6 - UX State and Operator Messaging Completion

Goal: make the secured pairing and backend-switch model understandable from both the frontend and the Companion.

Tasks:

- [x] 6.1 Add frontend UI states for `not paired`, `pairing code required`, `pairing expired`, `pairing failed`, `paired`, and `re-pair blocked while recording`. The frontend now distinguishes `not paired`, `pairing code required`, `pairing expired`, `pairing failed`, `connected`, `temporarily disconnected but still paired`, and `re-pair blocked while recording or upload completion is still active`, with the pairing and status controls consolidated under Settings -> Companion App and an explicit manual-disconnect signal that drops the browser back to `not paired` immediately when the Companion app intentionally disconnects. (was 7.11)
- [x] 6.2 Add notification copy for pairing success, pairing failure, pairing expiry, manual unpair, and backend switch completion. Frontend toasts now cover pairing success, pairing failure, pairing expiry, and pending-pairing recovery guidance, while the Companion surfaces pairing expiry, pairing lockout failure, manual unpair, pairing refresh, and backend switch completion with explicit copy about what remains active and what the user should do next. (was 8.6)

Exit criteria:

- [x] The frontend and Companion expose all required steady-state, blocked-state, and failure-state UX for the new pairing model.
- [x] Users can understand how to switch from one backend to another by re-pairing.

## Phase 7 - Strict Versioning and Verification

Goal: enforce strict version parity, mandate a clean re-pair for all upgrading users, and close the remaining evidence gaps.

Tasks:

- [x] 7.1 Add strict version mismatch messaging instructing users to update to the latest Companion and requiring a fresh re-pair.
- [x] 7.2 Implement a hard reset path that ignores legacy trust state and forces a clean first-pair workflow for upgrading users.

## Phase 8 - Documentation Closure

Goal: update the product, operator, developer, and automation documentation to match the shipped behavior.

Tasks:

- [ ] 8.1 Update `ARCHITECTURE.md` to describe the manual Companion-initiated pairing model and the one-backend association. (was 10.5)
- [ ] 8.2 Update `PRD.md` to reflect that anonymous Companion detection is removed and pairing is code-based and manual. (was 10.6)
- [ ] 8.3 Update `USAGE.md` to explain how users pair and re-pair the Companion when switching between deployments. (was 10.7)
- [ ] 8.4 Update `GETTING_STARTED.md` to explain the new first-pair workflow. (was 10.8)
- [ ] 8.5 Update `ADMIN.md` to explain operational impacts, including re-pair requirements after backend switching or security resets. (was 10.9)
- [ ] 8.6 Update `DEVELOPMENT.md` to explain the local development re-pair workflow when moving between dev and prod backends on one machine. (was 10.10)
- [ ] 8.7 Update `SECURITY.md` if the documented local-attack posture, pairing safeguards, or operator expectations materially change. (was 10.11)
- [ ] 8.8 Update `AGENTS.md` so future automation work follows the new pairing and localhost security model. (was 10.12)
- [ ] 8.9 Update `README.md` documentation index entries if the user journey for Companion pairing changes meaningfully. (was 10.13)
- [ ] 8.10 Add an operator-facing migration note if one-time re-pairing is required after the upgrade. (was 10.14)

Exit criteria:

- [ ] The final documented behavior matches the shipped implementation.
- [ ] Operator and developer documentation describe re-pairing, migration, and backend switching clearly.

## Cross-Phase Acceptance Criteria

The project should not be considered complete until all of the following are true:

- [x] An arbitrary web page cannot start, stop, pause, or resume a recording.
- [x] An arbitrary web page cannot silently re-pair the Companion to a different backend.
- [x] An unauthenticated frontend cannot determine that a Companion process is running on loopback.
- [x] Pairing can only be initiated manually from the Companion app.
- [x] Pairing requires a valid short-lived displayed code in the `ABCD-EFGH` format.
- [x] The Companion is associated with only one backend at a time.
- [x] Switching between development and production requires explicit re-pairing and never happens because of background polling.
- [x] Machine-local settings are preserved across backend switches where safe.
- [x] While a recording is active or uploading, re-pairing is blocked unless an explicitly approved override path is used.

## Implementation Notes

- The current bootstrap companion token and the per-recording upload token split should be preserved.
- The Companion should not continue to use a long-lived stored bootstrap token as its only durable trust anchor.
- Machine-local settings should remain separate from backend-specific paired state.
- Successful re-pair should replace backend-specific state atomically and clear stale secrets from the previously paired backend.
- No anonymous discovery or anonymous status surface should remain after the upgrade is complete.
