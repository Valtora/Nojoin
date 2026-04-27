# Companion Local HTTPS Upgrade

This document captures the remediation spec and implementation plan for replacing the Companion local API's plain loopback HTTP transport with local HTTPS.

## Audit Finding

The frontend currently calls the Companion local API over plain HTTP from the HTTPS-served Nojoin web UI.

- The browser-facing issue is mixed content. Modern browsers increasingly block secure pages from making insecure subresource requests to `http://127.0.0.1:12345`, which makes pairing and local control unreliable.
- The transport issue is plaintext loopback traffic. Even though the exposure is limited to the local machine, the pairing completion payload and local control material still cross loopback without TLS.

The original audit wording mentions a bootstrap JWT posted to `/auth`. That detail is stale relative to the current code. The current pairing flow now sends a revocable companion credential plus local control secret during `/pair/complete`, but the underlying finding still stands because the browser-to-Companion hop is still plain HTTP.

## Decision

The chosen remediation is local HTTPS for the Companion local API.

This preserves the current browser-to-Companion architecture while fixing both parts of the finding:

1. Mixed-content blocking from the HTTPS web UI.
2. Plaintext loopback transport of pairing and local control traffic.

## Locked Design Decisions

- The Companion generates and trusts a per-user local CA on first application launch, not in the installer.
- Trust bootstrap is preceded by a Nojoin-owned native explanation prompt before Windows shows its root-trust confirmation dialog.
- The local API fails closed if HTTPS cannot be established; there is no insecure HTTP fallback.
- Startup performs eager local HTTPS reconciliation before the listener binds.
- Missing trust for the existing CA is auto-repaired by re-installing that same CA into the current-user trust store.
- The CA remains stable on disk while the leaf certificate is renewed in place when nearing expiry.
- The CA validity window is 5 years, the leaf validity window is 180 days, and the leaf renews when fewer than 30 days remain.
- Local HTTPS material lives in a dedicated `local_https` area under Companion app data, separate from pairing secret storage.
- Identity continuity is anchored to the existing app-data path and bundle identity, not an additional cryptographic binding to the executable.
- The canonical browser target is `https://127.0.0.1:12345`, while the certificate also covers `localhost` and `::1`.
- The existing `/pair/complete` route and local control token model remain unchanged except that transport becomes HTTPS.
- The user-facing recovery path is a single repair-oriented action, while technical failure detail stays in logs and internal status.
- A dedicated internal local HTTPS health model is surfaced through existing browser status and native settings surfaces rather than a new diagnostics API.
- Browser-facing local HTTPS readiness is exposed as a separate field rather than overloading the existing Companion runtime status enum.
- The browser-facing local HTTPS readiness field stays coarse and repair-oriented, while detailed certificate causes remain internal and in logs.
- The browser-facing `localHttpsStatus` field exposes exactly three values: `ready`, `repairing`, and `needs-repair`.
- The browser does not use backend-mirrored or other fallback channels to infer local HTTPS status when the local secure endpoint is unreachable.
- Chromium-based browsers are release-blocking compatibility targets for this remediation.
- Firefox is supported through an explicit Firefox Pairing Mode walkthrough that directs the user to enable Firefox support in Companion Settings.
- Nojoin and the Companion must not silently modify user browser configuration as part of this remediation.
- Best-effort cleanup of the generated local CA happens only on uninstall paths where the user explicitly chooses to delete app data.

## Alternatives Considered

### Full backend relay

- Stronger long-term security model.
- Eliminates browser-to-localhost traffic entirely.
- Significantly larger architectural change because recording controls, status, waveform, settings, device enumeration, and pairing bootstrap would need a new backend-mediated control plane.

### Manual local certificate trust

- Lower engineering cost than automated local trust installation.
- Poorer user experience and higher support burden.
- Better suited to development or advanced operators than the default product path.

### Local secure WebSockets

- Equivalent to local HTTPS for the hard part.
- A secure local WebSocket still requires a trusted local certificate.
- Does not reduce scope compared with secure local HTTPS.

## Goals

- Remove all browser-initiated Companion traffic over plain HTTP.
- Preserve the current pairing model, backend TLS pinning, local control token model, and upload flow.
- Avoid shipping any shared certificate or private key with Nojoin.
- Keep the Companion loopback listener local-only.
- Preserve normal upgrades and frequent local debug rebuilds without forcing repeated trust bootstrap.

## Non-Goals

- Replace the current control plane with a backend relay.
- Redesign pairing, upload, or recording ownership semantics.
- Add machine-wide certificate trust by default. The machine-wide trust change is only needed for Firefox and must stay user-triggered, clearly explained, and administrator-approved.
- Add support for non-Windows Companion platforms.
- Introduce an insecure HTTP fallback path in normal builds.

## Current State

- The frontend local API base is hard-coded to `http://127.0.0.1:12345`.
- The Companion local server binds loopback over plain HTTP.
- Pairing completion, status polling, recording controls, waveform reads, settings reads and writes, device enumeration, and update triggers all use the same local HTTP transport.
- Backend-side origin checks, local control tokens, backend TLS pinning, and upload tokens are already implemented and should remain unchanged.

## Specification

### Local Trust Model

- The Companion generates a per-user local certificate authority on first application launch.
- The Companion also generates a server leaf certificate signed by that local CA.
- The local CA is trusted only in the Windows current-user certificate store.
- The leaf certificate must include SANs for `localhost`, `127.0.0.1`, and `::1`.
- The leaf certificate must publish a local file-backed CRL distribution point so Windows Schannel can complete revocation checks without external network access.
- The leaf certificate is used only by the Companion local API listener.
- The CA validity window is 5 years.
- The leaf validity window is 180 days.
- The Companion renews the leaf certificate when fewer than 30 days remain, without rotating the CA, and refreshes the local CRL during startup reconciliation.

Using a per-user local CA instead of directly trusting a one-off leaf certificate keeps trust stable across leaf renewal, upgrades, and frequent local rebuilds.

### Key and Certificate Storage

- All private key material remains local to the Windows user profile.
- Private key material is stored under a dedicated `local_https` area in the Companion app-data directory.
- Private key material is protected with the existing DPAPI pattern already used for Companion secret material.
- Public certificate metadata and the local CRL sidecar may be stored alongside the encrypted private material for diagnostics and repair.
- Startup reconciliation may also publish the generated local CRL into the Windows current-user CA store so Schannel can validate the local chain without relying on external revocation infrastructure.
- A schema version should be stored for the local HTTPS identity so future certificate or storage changes can be migrated explicitly.
- The local HTTPS store remains separate from backend pairing secrets even if it reuses the same DPAPI helper pattern.
- Identity continuity is defined by the stable bundle identity and app-data path rather than an extra executable-binding mechanism.

### Listener Behavior

- The Companion local API continues to bind only to loopback.
- The listener remains on the existing port `12345`.
- The local server must serve HTTPS only.
- The Companion performs startup reconciliation before binding the HTTPS listener.
- There is no silent downgrade to plain HTTP.
- Existing host validation and origin checks remain in place.
- Existing local control token checks remain in place.

### Frontend Transport

- The frontend local API base changes from `http://127.0.0.1:12345` to `https://127.0.0.1:12345` and remains the canonical browser target.
- All existing local consumers must use the secure base URL.
- Pairing completion remains browser-to-Companion through the existing `/pair/complete` route, but the payload now crosses loopback over HTTPS.
- The backend-issued local control token protocol remains unchanged.
- No backend API contract change is required for the remediation itself.

### Browser and Network Behavior

- The public Nojoin origin, reverse proxy, and Cloudflare Tunnel behavior are separate concerns from the local certificate.
- The local certificate secures only browser-to-Companion traffic on the Windows machine.
- Existing backend certificate pinning behavior stays unchanged.
- The Companion local server must support preflight behavior needed by modern browsers when a secure page calls a secure loopback origin.
- Chromium-based browsers are part of the release-blocking compatibility matrix for this flow.
- Firefox support is provided through a separate Firefox Pairing Mode in the browser settings UI.
- Firefox Pairing Mode directs the user to enable Firefox support in Companion Settings. That Companion action explicitly installs the Nojoin local HTTPS CA into the Windows Local Machine trusted root store after user confirmation and Windows administrator approval.
- Firefox users still need `security.enterprise_roots.enabled=true` so Firefox imports trusted Windows machine roots, and Firefox should be restarted after the Companion action.
- Nojoin and the Companion do not silently modify browser preferences, policies, profiles, certificate stores, or other browser configuration as part of this remediation.

### Upgrade and Development Behavior

- The local HTTPS identity must persist across normal Companion upgrades.
- Startup reconciliation runs before listener bind so missing trust or renewable leaf state is repaired before the frontend connects.
- If the expected CA is present but trust has been removed, the Companion re-installs trust for that same CA automatically.
- If the existing CA material is malformed or unusable, the Companion fails closed and routes the user through repair rather than silently replacing trust in the background.
- Rapid local rebuilds must not cause certificate churn as long as the bundle identity and app-data path remain stable.
- Re-pairing with the backend is independent from the local HTTPS identity and should not be required just because the Companion binary was rebuilt.
- If app data is deleted or the bundle identity changes, the local HTTPS trust bootstrap may need to be performed again.

### Recovery and Diagnostics

- The Companion must expose a single user-visible repair action for local HTTPS rather than separate trust-reset and certificate-reset actions.
- The repair flow may automatically repair missing trust or renewable leaf state for the existing CA, but replacing an unusable CA should require explicit user confirmation.
- The frontend should surface a distinct error state for local HTTPS trust failures instead of a generic fetch failure.
- The Companion should track a dedicated internal local HTTPS health model and surface it through existing `/status` responses and native settings state.
- Browser-facing payloads should expose local HTTPS readiness in a separate field from the existing recording/runtime status field.
- Browser-facing payloads should keep local HTTPS readiness coarse rather than exposing full certificate failure taxonomy.
- Browser-facing payloads should expose `localHttpsStatus` using only `ready`, `repairing`, and `needs-repair`.
- While `localHttpsStatus` is `repairing`, the frontend should treat it as a quiet transient state with short auto-retry and no persistent warning.
- While `localHttpsStatus` is `needs-repair`, the frontend should show a persistent repair-oriented warning, disable local-control actions, and fall back to a much slower retry cadence.
- While `localHttpsStatus` is `needs-repair`, the frontend should use a fixed 60-second retry cadence.
- While `localHttpsStatus` is `needs-repair`, that state should override the normal paired or temporarily disconnected companion messaging in the browser UI.
- Full repair-oriented messaging should appear in the global alert layer and Companion settings, while Meeting Controls and similar action surfaces stay concise and primarily disabled.
- The browser remains instruction-only for local HTTPS repair; repair is initiated from the Companion UI rather than through a browser-triggered native action.
- When local HTTPS fetches fail and no local status payload is available, the browser should notify the user and refer them to troubleshooting or Companion-side repair steps rather than relying on backend-derived fallback state.
- When local HTTPS fetches fail and no local status payload is available, the browser should use the generic message: "Companion local connection is unavailable. Open Companion Settings and use the repair or troubleshooting steps."
- The persistent generic browser warning for no-payload local failures appears only after the existing startup grace period ends and more than 2 consecutive failures have occurred.
- User-facing status copy should stay repair-oriented rather than exposing raw certificate-state terminology by default.
- The Companion logs must not emit private key material, PEM blobs, or certificate secrets.
- The Companion should log enough metadata to diagnose missing trust, expired certificates, regeneration, repair events, and CA-replacement requirements.
- Technical failure detail should live primarily in logs rather than primary settings UI copy.

### Installer and Uninstall Behavior

- Local HTTPS identity generation and trust bootstrap happen on first run instead of inside the installer.
- Trust installation should be idempotent.
- Best-effort uninstall cleanup of the generated local CA happens only when the uninstall path also deletes app data.
- Normal uninstall without app-data deletion leaves the local HTTPS identity and trust anchor in place.

## Expected Touch Points

The remediation is expected to touch these areas:

- `companion/src-tauri/src/server.rs`
- `companion/src-tauri/src/tls.rs`
- `companion/src-tauri/src/secret_store.rs`
- `companion/src-tauri/src/config.rs`
- `companion/src-tauri/Cargo.toml`
- `companion/src-tauri/nsis/installer.nsi`
- `frontend/src/lib/companionLocalApi.ts`
- `frontend/src/lib/serviceStatusStore.ts`
- `frontend/src/components/MeetingControls.tsx`
- `frontend/src/components/LiveAudioWaveform.tsx`
- `frontend/src/components/settings/SettingsPage.tsx`
- `docs/ARCHITECTURE.md`
- `docs/SECURITY.md`
- `docs/PRD.md`
- `docs/GETTING_STARTED.md`
- `docs/DEVELOPMENT.md`

## Implementation Plan

### Step 1. Build the local TLS identity manager

Status: Complete.

This step is now implemented in the Companion, including the dedicated local HTTPS identity module, certificate generation and leaf renewal, DPAPI-protected private material storage, Windows trust installation and verification, startup reconciliation with fail-closed repair behavior, the native pre-prompt before the Windows trust confirmation, and identity lifecycle tests.

#### Task 1.1. Add a local HTTPS identity module

Sub-tasks:

- Create a dedicated module for local HTTPS identity management rather than overloading backend TLS pinning code.
- Define a versioned data model for the local CA certificate, CA private key, leaf certificate, leaf private key, thumbprints, and validity timestamps.
- Keep the storage path under a dedicated `local_https` area inside the existing Companion app-data directory.

#### Task 1.2. Implement certificate generation

Sub-tasks:

- Add a Rust certificate-generation dependency suitable for self-generated local certificates.
- Generate a per-user local CA certificate and key.
- Generate a leaf certificate signed by that CA with SANs for `localhost`, `127.0.0.1`, and `::1`.
- Set the CA validity window to 5 years.
- Set the leaf validity window to 180 days.
- Renew the leaf when fewer than 30 days remain so routine startup repair does not cause constant regeneration.

#### Task 1.3. Reuse the DPAPI storage pattern for private material

Sub-tasks:

- Reuse the existing Windows DPAPI protection approach already implemented for Companion secrets.
- Store encrypted private key material separately from public certificate metadata.
- Keep the local HTTPS material separate from backend pairing secret storage even if helper code is shared.
- Add atomic write and replace behavior so certificate updates are not partially written.

#### Task 1.4. Implement Windows trust installation and trust verification

Sub-tasks:

- Add current-user certificate-store import for the generated local CA certificate.
- Add a lookup path to verify whether the expected CA is already trusted.
- Make trust installation idempotent so startup repair can safely re-run it for the same CA.
- Re-install trust automatically when the expected CA exists locally but is missing from the current-user trust store.
- Capture and surface actionable failure messages when trust installation or lookup fails.

#### Task 1.5. Add startup reconciliation and repair

Sub-tasks:

- On startup, reconcile the local HTTPS identity before binding the listener.
- If the CA is present but the leaf is missing, expired, or within the renewal threshold, regenerate only the leaf.
- If the CA is present but no longer trusted, re-install trust for that same CA.
- If the identity is malformed or unrecoverable, fail closed and surface a repair path instead of silently falling back to HTTP.
- Require explicit user confirmation before replacing an unusable CA rather than rotating trust silently.

#### Task 1.6. Add tests for identity lifecycle behavior

Sub-tasks:

- Add unit tests for identity serialization and versioning.
- Add unit tests for regeneration thresholds and startup repair behavior.
- Add Windows-only tests or narrow wrappers for DPAPI and trust-store integration where practical.

### Step 2. Convert the Companion local server from HTTP to HTTPS

Status: Complete.

#### Task 2.1. Add TLS server support for the loopback listener

Sub-tasks:

- Add a TLS acceptor around the existing Axum server.
- Load the leaf certificate and private key from the local HTTPS identity manager.
- Keep the listener bound to loopback only on the existing port `12345`.

#### Task 2.2. Remove plain HTTP as the normal transport

Sub-tasks:

- Replace the current plain HTTP startup path with HTTPS startup.
- Remove any implicit assumption in logs or diagnostics that the server is running on `http://`.
- Ensure startup fails clearly if the TLS identity cannot be loaded.

#### Task 2.3. Preserve existing local API security guards

Sub-tasks:

- Keep current loopback host validation.
- Keep current Origin validation.
- Keep the current local control bearer-token checks.
- Verify the existing `/pair/complete` route and steady-state routes still behave the same after the transport swap.

#### Task 2.4. Add browser-compatibility headers and preflight handling

Sub-tasks:

- Verify CORS behavior over HTTPS matches the current local API rules.
- Add any required handling for secure-origin preflights to secure loopback.
- Confirm that the browser can successfully call the Companion local API from the HTTPS-served Nojoin frontend without a manual browser exception.

#### Task 2.5. Add transport-level tests

Sub-tasks:

- Add tests for TLS listener startup with generated identity material.
- Add tests for pairing and steady-state routes over HTTPS.
- Add regression tests ensuring there is no normal HTTP listener path left behind.

### Step 3. Update the frontend local transport layer

Status: Complete.

The frontend local API helper now targets `https://127.0.0.1:12345`, and the audited frontend consumers for pairing completion, status polling, recording controls, waveform polling, Companion settings, device enumeration, and update triggers all route through the secure local API path while preserving existing request shapes and local control token usage. When a local HTTPS request fails before a local status payload is available, browser-facing surfaces use the repair-oriented message: "Companion local connection is unavailable. Open Companion Settings and use the repair or troubleshooting steps." Firefox-specific pairing guidance remains explicit opt-in through Companion Firefox Support and Firefox Windows root trust.

#### Task 3.1. Switch the local API base URL to HTTPS

Sub-tasks:

- Update the Companion base URL in the frontend local API helper to `https://127.0.0.1:12345`.
- Ensure all local API paths still resolve correctly after the scheme change.
- Confirm that credentials, headers, local control tokens, and token refresh logic remain unchanged.

#### Task 3.2. Audit every frontend consumer of the local API helper

Sub-tasks:

- Update pairing completion flows.
- Update status polling flows.
- Update recording control flows.
- Update waveform polling flows.
- Update settings, device enumeration, and update-trigger flows.
- Keep the existing `/pair/complete` request shape and local control token usage unchanged while swapping only the transport.

#### Task 3.3. Improve user-facing failure modes

Sub-tasks:

- Distinguish a local HTTPS trust failure from a generic offline Companion failure.
- Keep current pairing-ended and disconnected handling intact.
- Provide a message that points the user to Companion recovery if local trust is missing or stale.

#### Task 3.4. Re-run frontend build verification

Sub-tasks:

- Run the frontend production build after the transport change.
- Fix any new type or build errors caused by updated error handling or status models.

#### Task 3.5. Document browser support boundaries

Sub-tasks:

- Document Chromium-based browsers as the supported browser target for this remediation.
- Document Firefox support through explicit Firefox Pairing Mode.
- Document that Firefox users must opt in to the Companion Firefox Support action and Firefox Windows root trust before local HTTPS pairing can succeed.
- Document that Nojoin and the Companion do not silently change user browser configuration as part of the local HTTPS upgrade.

### Step 4. Add Companion diagnostics and recovery flows

Status: Complete.

The Companion now tracks a dedicated local HTTPS health model, surfaces coarse browser-facing `localHttpsStatus` readiness through local `/status` and native settings, and provides a native Repair Local HTTPS action with explicit confirmation before CA replacement. The local HTTPS listener now runs under a repair-aware controller so successful repair can restart the secure listener without falling back to HTTP, while browser-facing surfaces treat `repairing` as a quiet transient state and `needs-repair` as a persistent repair-oriented override with disabled local controls and a slower retry cadence. For developer recovery, deleting the Companion app-data `local_https` directory forces a fresh local HTTPS bootstrap on next launch, and changing the bundle identity or app-data path has the same effect; rebuilds that keep the same bundle identity and app-data path reuse the existing local HTTPS identity.

#### Task 4.1. Add Companion status signals for local HTTPS readiness

Sub-tasks:

- Track a dedicated internal local HTTPS health model.
- Track whether current-user trust is installed for the expected CA.
- Surface the health model through existing `/status` responses, native settings state, and tray-visible status where appropriate.
- Keep local HTTPS readiness separate from the existing Companion runtime status enum in browser-facing state.
- Keep the browser-facing local HTTPS field coarse and map detailed causes internally.
- Map the browser-facing local HTTPS field to exactly `ready`, `repairing`, and `needs-repair`.
- Treat `repairing` as a transient browser state with short retry cadence and no persistent warning.
- Treat `needs-repair` as a persistent browser-visible repair state with disabled local controls and slow background retries.
- Use a fixed 60-second retry cadence while `localHttpsStatus` is `needs-repair`.
- Let `needs-repair` take precedence over the normal paired/disconnected companion browser messaging.
- Show the full repair-oriented override in global alerts and Companion settings, but keep action-heavy surfaces concise and primarily disabled.
- Keep the browser repair flow instruction-only and direct the user to Companion Settings rather than adding a browser-triggered settings-launch action.
- Do not add backend-derived fallback status for cases where local HTTPS is unreachable.
- Reuse the existing service-alert threshold pattern so the persistent generic browser warning only appears after startup grace and more than 2 consecutive failures.

#### Task 4.2. Add a user-visible reset and repair action

Sub-tasks:

- Add a single Companion repair action for local HTTPS.
- Let the repair flow automatically fix missing trust or renewable leaf state for the current CA when safe.
- Prompt before replacing an unusable CA or regenerating trust roots.
- Ensure repair behavior does not accidentally clear paired backend trust state unless explicitly intended.

#### Task 4.3. Improve log messages and support diagnostics

Sub-tasks:

- Log certificate generation, repair, and renewal events.
- Log trust installation success and failure with enough context to debug.
- Keep technical certificate-state detail in logs while the UI stays repair-oriented.
- Avoid logging raw certificate PEM, private keys, or secret contents.

#### Task 4.4. Document developer recovery behavior

Sub-tasks:

- Document what happens when app data is deleted.
- Document what happens when bundle identity changes during development.
- Document how to force regeneration for local testing.

### Step 5. Handle installer, upgrade, and uninstall behavior

Status: Complete.

The Windows NSIS installer and uninstall flow are now aligned with the local HTTPS design. Normal upgrades preserve the Companion app-data area and therefore preserve the `local_https` identity unless the user explicitly removes app data. Trust bootstrap remains in the first-run application path rather than the installer, so startup reconciliation, upgrade, repair, and debug rebuilds continue to reuse the same runtime logic. The uninstall path now performs best-effort local HTTPS trust cleanup only when the user selects delete app data, removing the generated current-user CA and CRL before deleting the persisted local HTTPS files. Manual installer testing confirmed the expected upgrade, uninstall, and delete-app-data behavior.

#### Task 5.1. Preserve the local HTTPS identity across upgrades

Sub-tasks:

- Verify the current installer and updater path preserves the relevant app-data directory.
- Confirm local HTTPS identity files survive normal upgrades.
- Confirm the local HTTPS identity is not regenerated unnecessarily during update install.

#### Task 5.2. Decide where trust bootstrap happens

Sub-tasks:

- Implement trust bootstrap in the first-run application path rather than the installer.
- Keep the behavior idempotent so startup reconciliation, upgrade, and repair paths can reuse the same code.

#### Task 5.3. Add best-effort cleanup behavior

Sub-tasks:

- Remove the generated local CA from the current-user store only on uninstall paths where the user also chose delete app data.
- Tie cleanup to the existing delete-app-data uninstall path so thumbprint metadata is still available when cleanup runs.
- Treat cleanup as best-effort so uninstall still succeeds if CA removal fails.

#### Task 5.4. Validate debug-build behavior

Sub-tasks:

- Confirm local debug builds reuse the same app-data path and local HTTPS identity where possible.
- Document the cases where a new build path or bundle identifier causes trust bootstrap to repeat.
- Ensure rapid rebuilds do not require backend re-pairing unless app data was wiped.

### Step 6. Update documentation and verification guidance

#### Task 6.1. Update architecture and security docs

Sub-tasks:

- Update architecture documentation to describe the Companion local API as HTTPS on loopback.
- Update security documentation to describe the per-user local HTTPS identity and current-user trust model.
- Update the PRD so the stated transport model matches implementation.

#### Task 6.2. Update getting-started and development docs

Sub-tasks:

- Document the automatic one-time local trust bootstrap behavior and passive notification for normal users.
- Document how the local HTTPS identity behaves across upgrades.
- Document the expected local recovery steps for developers.

## Acceptance Criteria

- No browser-initiated Companion request remains on plain `http://127.0.0.1:12345`.
- The HTTPS-served Nojoin web UI can pair with, query, and control the Companion at `https://127.0.0.1:12345` without browser mixed-content errors.
- Pairing completion, status polling, waveform reads, settings operations, device enumeration, and update triggers succeed over secure loopback transport.
- The Companion local HTTPS identity is generated per user, persists across upgrades, and does not churn during normal debug rebuilds that preserve app data.
- The existing `/pair/complete` flow and local control token protocol remain unchanged apart from the transport scheme.
- Chromium-based browsers work in the supported setup without requiring a manual browser security exception.
- Firefox works after the user explicitly enables Firefox Support in Companion Settings and Firefox Windows root trust through Firefox Pairing Mode.
- Uninstall removes the generated local CA only when the user explicitly chooses app-data deletion, and otherwise leaves local trust material intact.
- Backend pairing, backend TLS pinning, upload-token behavior, and recording ownership semantics remain unchanged.

## Open Questions to Resolve During Implementation

- None at the architecture/spec level. Remaining decisions are implementation details discovered during coding and validation.
