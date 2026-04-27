# Companion UX Upgrade Plan

This document captures the UX polish pass that follows the completed local HTTPS remediation work. The transport and security work is effectively complete and is treated here as a fixed input. This plan does not reopen the HTTPS architecture. It focuses on making install, first launch, browser choice, pairing, repair, and steady-state operation clearer and lower-friction.

## Review Outcome

- The completed local HTTPS remediation should remain documentation and verification focused.
- The local HTTPS architecture is already fixed and should be treated as a UX constraint, not an open design area.
- The main UX problems are fragmented guidance, inconsistent state presentation, weak install-to-pair handoff, and too much cross-referencing between native windows, browser settings, alerts, and docs.
- The current implementation already has the necessary primitives for a strong UX pass: coarse local HTTPS states, explicit repair actions, Firefox support actions, pairing-window lifecycle handling, dashboard disabled states, and browser-side pairing/error messaging.

## Inputs Reviewed

- The completed local HTTPS remediation work and its resulting product constraints
- [GETTING_STARTED.md](GETTING_STARTED.md)
- [USAGE.md](USAGE.md)
- [DEVELOPMENT.md](DEVELOPMENT.md)
- [SECURITY.md](SECURITY.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [PRD.md](PRD.md)
- [ADMIN.md](ADMIN.md)
- companion/src/index.html
- companion/src/settings.html
- companion/src/pairing.html
- companion/src-tauri/src/main.rs
- frontend/src/components/settings/CompanionAppSettings.tsx
- frontend/src/components/settings/AudioSettings.tsx
- frontend/src/components/settings/SettingsPage.tsx
- frontend/src/components/MeetingControls.tsx
- frontend/src/components/LiveAudioWaveform.tsx
- frontend/src/components/ServiceStatusAlerts.tsx
- frontend/src/lib/serviceStatusStore.ts
- frontend/src/lib/companionLocalApi.ts
- frontend/src/lib/platform.ts
- frontend/src/lib/tour-config.ts

## Locked UX Constraints

- The Companion local API remains HTTPS-only on `https://127.0.0.1:12345`.
- There is no insecure HTTP fallback.
- Pairing remains manual and user-initiated from the Companion app.
- The browser must not silently detect an unpaired Companion through anonymous localhost probing.
- Firefox support remains explicit opt-in and requires both a Companion-side action and Firefox-side enterprise roots configuration.
- Nojoin and the Companion must not silently modify browser preferences or other browser configuration.
- Browser repair remains instruction-only. Repair is triggered from the Companion UI, not from the web app.
- Browser-facing local HTTPS state remains coarse and limited to `ready`, `repairing`, and `needs-repair`.
- Technical certificate-state detail should remain primarily in logs rather than becoming end-user UI terminology.

## Current-State Findings

### 1. Install-to-pair handoff is weak

- [GETTING_STARTED.md](GETTING_STARTED.md) and [USAGE.md](USAGE.md) describe the broad flow, but they do not prepare the user for the local trust bootstrap, supported browser expectations, or what happens after first launch.
- The web app exposes download links, but the install story is still spread across Updates, Settings, tours, and docs rather than one canonical path.

### 2. The native Companion landing window is too passive

- companion/src/index.html currently acts like a placeholder splash that says the app is running in the tray.
- That window does not help a first-time user choose the next step, understand pairing state, or recover from repair-required states.

### 3. Native Settings has the right controls but weak hierarchy

- companion/src/settings.html correctly surfaces pairing, repair, Firefox support, and disconnect actions.
- Those actions currently carry similar visual weight even though they represent very different risk and urgency levels.
- The page communicates state accurately, but it still reads like a status panel rather than a guided task flow.

### 4. The pairing flow is still cross-app and cognitively expensive

- companion/src/pairing.html has a good code display, copy action, and expiry countdown.
- The user still has to mentally stitch together which app to use next, whether the current backend stays active during re-pairing, and what differs for Firefox.

### 5. The tray context menu is too busy for its role

- The Companion tray menu currently mixes status, recording controls, settings, run-on-startup, updates, logs, about, and quit in one flat list.
- During recording it becomes even denser because pause, resume, and stop controls are inserted above the same utility actions.
- That makes the context menu feel more like a command dump than a fast, glanceable control surface for the few tasks users actually need from the tray.

### 6. The web Companion settings page is accurate but text-heavy

- frontend/src/components/settings/CompanionAppSettings.tsx contains most of the needed logic.
- The current page mixes download, browser selection, pairing, version mismatch, repair, and audio preferences in one long form.
- The result is functionally correct but still heavier than it should be for first-time users.

### 7. Steady-state surfaces disable correctly but do not always guide recovery well

- frontend/src/components/MeetingControls.tsx and frontend/src/components/ServiceStatusAlerts.tsx disable or warn in the right conditions.
- The next step is not always obvious from the action surface itself, especially for repair-required, disconnected, or first-pair cases.
- frontend/src/components/LiveAudioWaveform.tsx quietly drops to zeroed samples on local fetch failure, which is technically safe but not especially communicative.

### 8. Documentation and tours lag the implemented security model

- [GETTING_STARTED.md](GETTING_STARTED.md) and [USAGE.md](USAGE.md) do not yet teach the local HTTPS bootstrap, repair flow, or Firefox path at the level users need.
- [PRD.md](PRD.md) still describes the Companion UI too narrowly as a minimalist tray menu, while the product now depends on richer Settings and repair interactions.
- The current tours mention Companion setup at a high level but do not explain browser-specific setup or repair-oriented recovery.

## UX Objectives

- Make the first successful Chromium-based install and pair path obvious without sending the user to internal design docs.
- Make the Firefox path explicit before the user fails, not after.
- Ensure every major state has one primary next action.
- Keep terminology consistent across native windows, browser UI, notifications, and docs.
- Reduce how often users must infer whether they need install, pair, re-pair, reconnect, update, or repair.
- Preserve the fail-closed local HTTPS model without exposing unnecessary certificate jargon.
- Keep advanced detail available for operators and developers without making first-run UX feel operationally dense.

## Non-Goals

- Replacing the browser-to-Companion control path with a backend relay.
- Changing the local HTTPS trust model, validity windows, or repair authority boundaries.
- Reopening the `/pair/complete` contract or local control token model.
- Adding silent browser reconfiguration, insecure fallback transport, or browser-launched native repair.
- Expanding Companion support beyond Windows as part of this pass.

## Waterfall Development Rules

- Each step below is sequential. Do not start a later step until the current step is implemented, reviewed, and manually validated.
- If a later step uncovers a missing prerequisite, pull that work back into the earliest unfinished step instead of jumping ahead and leaving dependency holes.
- Native utility destinations must exist before tray items are removed.
- The native ownership model must be stable before the web Companion page is restructured around it.
- Documentation should be finalized only after the implemented UI, copy, and validation behavior stop moving.

## Dependency Tree

- Step 1 unlocks every later step because it defines the copy, state, ownership, and CTA contract.
- Step 2 unlocks Step 3 because the launcher UI needs its runtime state and action plumbing first.
- Step 4 must land before Step 5 because tray utilities are being moved into Settings.
- Steps 3 through 6 complete the native-first experience before Step 7 reshapes the secondary web surface around that model.
- Step 7 must land before Step 8 because the steady-state web surfaces should reuse the final card structure, copy, and support boundaries.
- Step 9 validates the integrated experience before Step 10 freezes the user-facing documentation.

## Waterfall Implementation Plan

### Step 1. Define the cross-surface UX contract

Status: Complete.

This step is complete. The contract below is the canonical downstream input for Steps 2 through 10. Later steps must reuse these state names, CTA labels, ownership boundaries, and handoff rules unless this step is explicitly revised first.

Step 1 exit gate: manual review and sign-off are required before Step 2 begins.

#### Task 1.1. Build the canonical Companion state matrix

Status: Complete.

Contract notes:

- `First run` is an onboarding presentation variant of `Not paired`. It is only used until the user has seen the initial orientation once after install or trust reset.
- `Temporarily disconnected` means pairing is still valid and should recover automatically. It must not be described as unpaired.
- `localHttpsStatus` remains an implementation field. User-facing copy must use `Browser repair in progress` and `Browser repair required`.
- `Version mismatch` means local control is blocked until Nojoin and the Companion are brought back onto a compatible build pair.
- `Backend switch blocked` is shown only when the user explicitly tries to replace an existing pairing.

| State | Primary surfaces | User-facing status | Primary CTA | Secondary CTA | Blocked actions | Fallback guidance | Notification intent |
| --- | --- | --- | --- | --- | --- | --- | --- |
| First run | Launcher | Welcome. Companion is installed but not connected yet. | `Start Pairing` | `Open Settings` | `Open Nojoin` as a healthy-state shortcut; browser-side recording controls; backend-switch actions | If pairing will happen later, the user can close the launcher and reopen from the tray. The next open uses the standard unpaired state. | None on explicit launch; one-time informational reminder only on first autostart. |
| Unpaired | Launcher, web Companion page | `Not paired` | `Start Pairing` | `Open Settings` | Local recording controls; replacement-pair flows; recovery messaging that assumes an active backend | Leave Companion idle and start pairing later from the launcher or Settings. | One reminder on autostart until first successful pair; no repeated toast loop during active use. |
| Pairing active | Pairing window, web Companion page | `Pairing code active` | `Open Nojoin` | `Cancel Pairing` | Submitting an expired code; disconnect or repair work from the pairing window; opening a second local pairing session | If this is replacement pairing, keep stating that the current backend stays active until the new pairing succeeds. | Informational notice when the code is opened; warning only if the code expires or is cancelled. |
| Paired and healthy | Launcher, tray, web Companion page | `Connected` | `Open Nojoin` | `Open Settings` | Launcher-side repair, Firefox support, disconnect, and low-frequency utility actions | Route configuration, support, and destructive work into Settings and keep the launcher compact. | No proactive notification; tray/status text only. |
| Paired and disconnected | Launcher, web Companion page, Meeting controls | `Temporarily disconnected` | `Open Settings` | `Open Nojoin` | New local-control actions that require a live Companion response; backend switching until state is clear | Tell the user the pairing is still valid and will resync automatically; do not escalate to repair first. | Single informational notification on state transition only; no persistent warning while reconnecting. |
| Local HTTPS repairing | Launcher, web Companion page, alerts | `Browser repair in progress` | `Open Settings` | None | Re-triggering repair from other surfaces; browser-side local control until repair completes | Wait for automatic refresh and keep the state quiet unless it exceeds normal repair time. | One informational transition notice; suppress repetitive alerts. |
| Local HTTPS needs repair | Launcher, web Companion page, Meeting controls | `Browser repair required` | `Open Settings to Repair` | `Open Settings` | Browser-side local control; pairing completion from the web app; launcher happy-path actions other than routing to Settings | Open Settings, run the repair flow there, then retry the original task after status returns to `Connected`. | Persistent warning until resolved; repeat only on state re-entry or explicit retry. |
| Version mismatch | Launcher, Settings, web Companion page | `Version mismatch` | `Open Settings` | `Open Nojoin` | Local recording controls; pairing completion if compatibility checks fail; backend switching until versions align | Update the older side first. If the upgrade clears trust state, generate a new pairing code after updating. | Warning on detection and after blocked local-control attempts; never on every poll cycle. |
| Backend switch blocked by recording | Settings, web Companion page | `Backend switch blocked while recording` | `Open Nojoin` | `Open Settings` | `Generate New Pairing Code` for replacement pairing; `Disconnect Current Backend` as a shortcut around the block | Stop or finish the active recording before switching this machine to a different backend. | Immediate warning only when the blocked switch is attempted. |
| Backend switch blocked by upload | Settings, web Companion page | `Backend switch blocked until upload finishes` | `Open Nojoin` | `Open Settings` | `Generate New Pairing Code` for replacement pairing; `Disconnect Current Backend` as a shortcut around the block | Wait for the queued upload to finish, then retry. Keep the current backend attached until upload clears. | Immediate warning only when the blocked switch is attempted. |
| Firefox prerequisite incomplete | Settings, web Companion page | `Firefox setup incomplete` | `Enable Firefox Support` | `Open Settings` | Firefox-based pairing confirmation and Firefox local control; the default Chromium path remains available | Enable Firefox Support in Settings, turn on Firefox enterprise roots, restart Firefox, then generate a fresh pairing code. | Contextual warning inside the Firefox branch only; never a global tray or launcher alarm. |
| Pairing expired | Pairing window, web Companion page | `Pairing expired` | `Generate New Pairing Code` | `Open Settings` | Submitting the expired code; resuming the expired local pairing session | Start a fresh code from Settings. If this was replacement pairing, the current backend stays connected. | One warning on expiry; no repeated reminders after dismissal. |

State precedence for any surface that can see multiple conditions at once:

1. `Browser repair required`
2. `Version mismatch`
3. `Backend switch blocked` states
4. `Firefox setup incomplete` on the Firefox branch only
5. `Pairing code active`
6. `Pairing expired`
7. `First run` / `Not paired`
8. `Temporarily disconnected`
9. `Browser repair in progress`
10. `Connected`

#### Task 1.2. Freeze surface ownership boundaries

Status: Complete.

| Surface | Owns | Must not own | Required handoff output |
| --- | --- | --- | --- |
| Launcher | Native-first orientation, one primary next action, compact backend summary, blocking-state visibility on explicit launch | Direct repair execution, Firefox branching, low-frequency utilities, destructive actions, long-form setup copy | `Start Pairing` / `Generate New Pairing Code` open the pairing window; `Open Settings` routes to Settings; `Open Nojoin` routes to the paired public origin |
| Settings | Configuration, troubleshooting, Firefox support, utility actions, destructive actions, detailed pairing and repair context | First-run wizard ownership, tray-like operational density, browser-side verification, duplicated launcher summary copy | Pairing actions open the pairing window; repair and Firefox actions stay native; disconnect returns the app to an unpaired state |
| Tray | Fast operational fallback, glanceable status, active recording controls, `Open Nojoin`, `Settings`, `Quit` | Onboarding, browser selection, Firefox instructions, repair execution, utilities at top level, destructive actions | Recording controls act immediately; `Open Nojoin` and `Settings` route to their owning surfaces |
| Pairing window | Current pairing code, countdown, cancel action, concise next-step guidance, replacement-pair continuity note | Browser-specific branching, repair, settings utilities, destructive actions, long-form troubleshooting | Successful pairing hands control to Nojoin and closes the window; cancel or expiry returns control to the initiating native surface |
| Web Companion page | Browser-side state card, pairing code entry, Firefox support card, steady-state verification, degraded web guidance | Canonical first-run ownership, native repair execution, native pairing initiation, destructive native actions | When a native-only action is required, the page instructs the user with frozen CTA labels and routes them back to the owning native surface |

Ownership boundary summary:

- The launcher is the canonical owner of first-run orientation and the single primary next action.
- Settings is the canonical owner of all support, configuration, utility, Firefox, and destructive work.
- The tray is an operational fallback surface, not a second settings page.
- The pairing window owns only the active code lifecycle and handoff into Nojoin.
- The web Companion page is the secondary browser-side state surface. It confirms, warns, and guides, but it does not replace the native-first model.

Native window sizing rule:

- Every Companion-owned native webview window must size itself to its current content instead of opening on a fixed oversized canvas.
- Launcher, Settings, pairing, and later native support windows must open at the smallest size that fully fits their current content and re-measure after state-driven content changes.
- Manual freeform resizing is not part of the primary UX contract for these task-oriented windows.

#### Task 1.3. Freeze terminology and CTA vocabulary

Status: Complete.

Action labels that downstream implementation must use exactly:

| Use exactly | Use when | Retire or avoid |
| --- | --- | --- |
| `Start Pairing` | The Companion has no active backend pairing and no current local code | `Pair with Nojoin`, `Connect Companion`, `Begin Pairing` |
| `Generate New Pairing Code` | The user needs a fresh code after expiry or while replacing an existing backend | `Start Re-pairing`, `New Pairing`, `Regenerate Code` |
| `Open Nojoin` | Native surfaces send the user back to the web app | `Open Browser`, `Return to Website`, `Open Dashboard` |
| `Open Settings` | Any surface routes the user into native configuration or support | `Preferences`, `Companion Options`, `Manage Companion` |
| `Open Settings to Repair` | A non-Settings surface detects repair-required state and must route the user to the native repair flow | `Repair Now`, `Fix HTTPS Here`, `Resolve Certificate Error` |
| `Repair Local Browser Connection` | Settings executes the actual repair flow | `Repair Local HTTPS` in end-user copy outside implementation detail |
| `Enable Firefox Support` | Settings runs the privileged Firefox prerequisite action | `Trust Firefox`, `Enable Local Firefox Pairing`, `Fix Firefox Certificate` |
| `Disconnect Current Backend` | Settings exposes the destructive unpair and revoke action | `Unpair Companion`, `Remove Connection`, `Reset Pairing` |
| `Cancel Pairing` | The user cancels the active local pairing session | `Cancel Local Pairing Session` |

Status labels that downstream implementation must keep consistent:

| Condition | Status label | Required helper language |
| --- | --- | --- |
| First run | `Set up Companion` | Explain that Companion is installed but not connected yet. |
| Unpaired | `Not paired` | Tell the user to start pairing from the Companion first. |
| Pairing active | `Pairing code active` | Tell the user to finish pairing in Nojoin with the current code. |
| Replacement pairing active | `Pairing code active` | Add that the current backend stays active until the new pairing succeeds. |
| Paired and healthy | `Connected` | Keep helper copy short and non-operational. |
| Paired and disconnected | `Temporarily disconnected` | Say the pairing remains valid and will resync automatically. |
| Local HTTPS repairing | `Browser repair in progress` | Say browser controls will refresh automatically when repair finishes. |
| Local HTTPS needs repair | `Browser repair required` | Send the user to Settings; do not explain certificate internals. |
| Version mismatch | `Version mismatch` | Say versions must be aligned before local control will work again. |
| Backend switch blocked by recording | `Backend switch blocked while recording` | Name the recording as the reason and tell the user to stop or finish it first. |
| Backend switch blocked by upload | `Backend switch blocked until upload finishes` | Name the queued upload as the reason and tell the user to wait. |
| Firefox prerequisite incomplete | `Firefox setup incomplete` | Name the sequence: enable support, restart Firefox, generate a fresh code. |
| Pairing expired | `Pairing expired` | Tell the user to generate a new code from the Companion. |

Message class rules:

- Status summaries are short labels only. They must not expose implementation terms such as `needs-repair`, `repairing`, `localHttpsStatus`, `TOFU`, or certificate-store names.
- Instructional helper lines tell the user exactly one next step or one automatic recovery expectation.
- Warnings are reserved for blocked actions, repair-required states, version mismatch, and expiry. Each warning must name both the blocking reason and the recovery path.
- `Temporarily disconnected` and `Browser repair in progress` are informational states, not warning states, unless they exceed expected recovery time.

#### Task 1.4. Freeze the implementation handoff rules

Status: Complete.

Launch rules:

- If a local pairing session is already active, an explicit app launch focuses the pairing window instead of opening a second launcher instance.
- Otherwise, an explicit app launch opens or focuses the launcher.
- An explicit `Settings` action from the tray or launcher always focuses Settings directly.

Autostart rules:

- `Connected`, `Temporarily disconnected`, and `Browser repair in progress` autostart quietly to the tray.
- `First run` and `Not paired` may auto-open the launcher once after background startup completes.
- Autostart never opens the pairing window or Settings on its own.
- `Browser repair required` and `Version mismatch` on autostart use tray and notification guidance only until the user explicitly opens a surface.

Launcher direct-action rules:

- The launcher may directly trigger only `Start Pairing`, `Generate New Pairing Code`, `Open Nojoin`, and `Open Settings`.
- `Open Settings to Repair` is a routing label, not a repair action. It must focus Settings and land the user in the troubleshooting section.
- The launcher must never directly execute `Repair Local Browser Connection`, `Enable Firefox Support`, `Disconnect Current Backend`, `Run on Startup`, `View Logs`, `Check for Updates`, or `About`.

Tray top-level rules:

- The tray top level always contains a non-action status line, active recording controls when relevant, `Open Nojoin`, `Settings`, and `Quit`.
- `Run on Startup`, `View Logs`, `Check for Updates`, `About`, `Enable Firefox Support`, `Repair Local Browser Connection`, and `Disconnect Current Backend` move out of the tray top level and into Settings.
- The tray never owns first-run explanation, browser branching, or repair guidance.

Surface transition rules:

- Launcher to pairing window: `Start Pairing` and `Generate New Pairing Code` open or focus the pairing window.
- Launcher to Settings: `Open Settings` and `Open Settings to Repair` always hand off to Settings rather than opening a parallel troubleshooting surface.
- Launcher to web: `Open Nojoin` opens the last paired public origin. If there is no paired backend, the launcher stays responsible for orientation instead of guessing a target URL.
- Settings to pairing window: pairing actions open or focus the pairing window. Settings remains the canonical restart surface after cancel or expiry.
- Pairing window to web: Nojoin is the completion surface. The pairing window never owns browser choice or Firefox branching.
- Pairing success: successful web completion closes the pairing window. If the user was replacing a backend, the new backend becomes active atomically only after that success.
- Pairing cancel or expiry: the pairing window closes and returns the user to the initiating native surface with either `Not paired` or `Connected` plus no active code, depending on whether a prior backend still exists.
- Web to native: the web Companion page may instruct `Open Settings`, `Open Settings to Repair`, `Enable Firefox Support`, or `Generate New Pairing Code`, but it must never directly execute repair, Firefox enablement, or disconnect.
- Repair-required handoff: only Settings may execute `Repair Local Browser Connection`. Every other surface may only route the user into Settings.

### Step 2. Add native launcher plumbing and state delivery

Status: Implemented. Manual validation pending.

This step prepares the runtime contract required before the launcher UI can be rebuilt.

Implementation notes:

- The Companion now exposes a dedicated launcher state command instead of overloading the Settings state payload.
- Manual startup, autostart, and repeated launches now route through explicit launcher or pairing focus rules, with autostart identified by a dedicated startup argument.
- Native `Open Nojoin` now opens only a real paired web origin, and trust resets return the launcher to first-run onboarding mode.

#### Task 2.1. Add a launcher-specific native view model

Status: Implemented.

Sub-tasks:

- Decide whether to extend the existing settings-state command or add a dedicated launcher-state command.
- Expose only the state needed by the launcher: backend summary, pairing state, local HTTPS state, and launcher CTA mode.
- Keep the launcher state aligned with the existing local HTTPS status model rather than inventing a parallel status taxonomy.

#### Task 2.2. Implement launcher window lifecycle rules

Status: Implemented.

Sub-tasks:

- Implement explicit-launch behavior for first-run and user-opened sessions.
- Implement the autostart exception rule for machines that start with no active pairing.
- Preserve quiet background autostart when the Companion is already paired and healthy.
- Define single-instance focus behavior so repeated launches bring the correct native window forward.

#### Task 2.3. Implement launcher action plumbing

Status: Implemented.

Sub-tasks:

- Wire direct launcher actions for `Start Pairing` / `Generate New Pairing Code`, `Open Nojoin`, and `Open Settings`.
- Wire the repair-state launcher CTA so it opens Settings rather than calling repair directly.
- Ensure launcher actions reuse the existing pairing and web-launch logic instead of duplicating it.

#### Task 2.4. Add runtime instrumentation and guardrails

Status: Implemented.

Sub-tasks:

- Log launcher auto-open decisions and major action transitions for debugging.
- Ensure launcher-triggered actions still respect existing pairing blocks and secure-local-health rules.
- Verify that launcher behavior does not regress tray-only operation.

### Step 3. Build the lightweight native launcher UI

Status: Implemented. Manual validation pending.

This step replaces the placeholder native home window once the launcher state and actions exist.

Implementation notes:

- companion/src/index.html now renders the launcher directly from `get_launcher_state()` and routes every CTA through the existing native launcher commands.
- The launcher remains compact while covering first-run, unpaired, healthy paired, disconnected, repair-in-progress, and repair-required states without adding browser-specific branching.
- The existing content-fit sizing rule remains in place and re-measures after state refreshes, action outcomes, and other content changes.

#### Task 3.1. Replace the placeholder launcher layout

Status: Implemented.

Sub-tasks:

- Replace the passive splash content in companion/src/index.html.
- Add a compact state-driven layout with a title, short explanation, current backend summary, and local browser-connection summary.
- Keep the window small enough to feel tray-appropriate rather than app-dashboard-like.

#### Task 3.2. Implement first-run and unpaired launcher states

Status: Implemented.

Sub-tasks:

- Add a short first-run explanation for what the Companion does and why pairing is required.
- Present `Start Pairing` as the primary CTA.
- Ensure the launcher explains the next step without immediately skipping into the pairing window.

#### Task 3.3. Implement healthy paired launcher state

Status: Implemented.

Sub-tasks:

- Show the paired backend summary.
- Present `Open Nojoin` as the primary CTA.
- Keep `Open Settings` available as the secondary CTA.

#### Task 3.4. Implement degraded launcher states

Status: Implemented.

Sub-tasks:

- Add a prominent `Open Settings to Repair` CTA when local HTTPS needs repair.
- Add copy for temporarily disconnected and replacement-pairing states.
- Ensure the launcher remains browser-agnostic and does not grow Firefox-specific branching logic.

#### Task 3.5. Polish launcher behavior

Status: Implemented.

Sub-tasks:

- Tune window sizing, focus behavior, and dismissal behavior.
- Ensure copy length fits the compact native surface.
- Verify the launcher remains useful without becoming a second Settings page.

### Step 4. Reorganize native Settings into main path, troubleshooting, and advanced sections

Status: Implemented. Manual validation pending.

This step gives low-frequency utilities and troubleshooting actions a clear home before the tray is simplified.

Implementation notes:

- Opening Settings now routes native window creation through the same main-thread lifecycle used by the pairing window, which resolves the launcher-triggered freeze observed during Step 4.1 investigation.
- Native Settings now uses the launcher's divider-based native window style instead of web-style card stacks. This should be treated as the downstream native-surface rule for the rest of the UX upgrade.
- Native Settings now keeps Pairing as the primary section, moves support and destructive flows behind a collapsed `Advanced` section, and keeps the frozen Step 1 CTA labels.
- `Run on Startup`, `View Logs`, `Check for Updates`, and `About` are now available from Settings through native commands. Tray cleanup remains deferred to Step 5.
- Native windows now follow the operating system light or dark appearance setting instead of forcing a light-only theme.

#### Task 4.1. Establish the Settings information architecture

Status: Implemented.

Resolved blocker in this task:

- Opening Settings no longer freezes the native Companion UI after the window appears. Settings window creation now uses the Companion's main-thread native window path instead of creating the webview directly from the launcher command handler.

Sub-tasks:

- Split the page into Pairing, device utilities, and a collapsed Advanced area for secure-local troubleshooting and destructive actions.
- Keep pairing actions in the primary path and remove redundant connection/backend presentation.
- Keep local HTTPS state visible without making certificate-repair actions look routine.

#### Task 4.2. Rebuild the main path section

Status: Implemented.

Sub-tasks:

- Promote the current backend summary and connection state.
- Keep start pairing and re-pair actions visually primary.
- Clarify replacement-pair messaging so the current backend continuity is explicit.

#### Task 4.3. Build the troubleshooting section

Status: Implemented.

Sub-tasks:

- Group local HTTPS repair and Firefox support together as support flows rather than routine actions.
- Make repair guidance action-oriented and consistent with launcher and web copy.
- Keep destructive actions out of this section.

#### Task 4.4. Add a utility/support section for low-frequency native actions

Status: Implemented.

Sub-tasks:

- Move `Run on Startup`, `View Logs`, `Check for Updates`, and `About` into Settings.
- Expose any new native commands needed so the Settings page can trigger those actions directly.
- Ensure these utilities are available before tray cleanup begins.

#### Task 4.5. Build the advanced/destructive section

Status: Implemented.

Sub-tasks:

- Move `Disconnect Current Backend` into an explicit advanced/destructive area.
- Add clearer cautionary copy and post-action expectations.
- Keep disconnect behavior visually and semantically separate from pairing and repair.

### Step 5. Simplify the tray menu into an operational fallback surface

Status: Complete.

This step should not begin until Step 4 has already given the displaced tray items a new home.

Implementation notes:

- The tray top level now contains only the non-action status line, active recording controls when relevant, `Open Nojoin`, `Settings`, and `Quit`.
- `Open Nojoin` stays disabled until the Companion has a real paired origin, and tray double-click now opens Nojoin only for paired deployments; otherwise it focuses the primary native surface.
- Tray status text and tooltip copy now use the frozen Step 1 vocabulary for `Connected`, `Not paired`, `Temporarily disconnected`, `Browser repair in progress`, and `Browser repair required`, while preserving queued-upload and reconnect wording during recording recovery.

#### Task 5.1. Reduce the top-level tray menu

Sub-tasks:

- Keep the current status item.
- Keep active recording controls when relevant.
- Add or preserve `Open Nojoin`, `Settings`, and `Quit`.
- Remove low-frequency utility items from the primary tray surface.

#### Task 5.2. Preserve recording and offline-recovery usability

Sub-tasks:

- Keep pause, resume, and stop actions easy to scan during active capture.
- Preserve special wording for queued-upload and reconnect states.
- Ensure invalid actions remain disabled rather than ambiguous.

#### Task 5.3. Align tray interaction behavior

Sub-tasks:

- Review double-click behavior against the new menu contract.
- Align tooltip/status text with the final native copy vocabulary.
- Ensure tray affordances remain coherent with launcher and Settings behavior.

#### Task 5.4. Regress tray-only workflows

Sub-tasks:

- Validate tray behavior during normal paired idle use.
- Validate tray behavior during active recording.
- Validate tray behavior during offline recovery and queued uploads.

### Step 6. Polish the pairing window and pairing lifecycle messaging

Status: Complete.

This step completes the native-first onboarding flow after launcher, Settings, and tray boundaries are stable.

Implementation notes:

- The pairing window now uses the same compact native layout pattern as the launcher and Settings, while keeping the large code display, copy action, countdown, and `Cancel Pairing` action.
- The window now differentiates first pairing from replacement pairing and explicitly states when the current backend stays active until the new pairing succeeds.
- Expiry and cancellation copy now route users back to Companion Settings with the correct restart action for the current variant, and successful pairing still closes the window immediately while native notifications carry the confirmation.

#### Task 6.1. Redesign the pairing window content

Sub-tasks:

- Keep the large code display, copy affordance, countdown, and cancel action.
- Add a short step sequence that explains what the user should do next in Nojoin.
- Keep the layout compact and focused on the handoff rather than secondary configuration.

#### Task 6.2. Clarify pairing variants in docs found in the /docs folder

Sub-tasks:

- Differentiate first pairing from replacement pairing.
- Make it explicit when the current backend stays active until replacement pairing succeeds.
- Keep Firefox-specific guidance lightweight in the pairing window because full browser branching stays elsewhere.

#### Task 6.3. Improve pairing lifecycle outcomes

Sub-tasks:

- Review expiry messaging.
- Review cancellation messaging.
- Review whether any success-state confirmation should be shown before the window closes.

### Step 7. Redesign the Settings -> Companion App page as state cards with one primary CTA

Status: Complete.

This step reshapes the secondary web surface after the native-first ownership model is already implemented.

Implementation notes:

- The web Companion page now resolves its top-level presentation through a single state-card view model that reuses the existing service and pairing state instead of stacking separate connection and pairing summaries.
- Firefox browsers now stay on the standard unpaired flow until a Firefox pairing attempt fails, at which point the page routes the user into a dedicated support card with the prerequisite order: enable Firefox Support in the Companion, turn on Firefox enterprise roots, restart Firefox, then generate a fresh pairing code.
- The default Chromium path is now the concise browser-side pairing path. The manual Standard versus Firefox toggle has been removed so the page no longer reads like a browser-selection wizard.
- Connection management remains the primary section, while installer and update tools are separated into their own support section and recording preferences remain in a distinct lower section.
- Shared browser-side copy now aligns with the frozen Step 1 state vocabulary for `Not paired`, `Temporarily disconnected`, `Browser repair in progress`, `Browser repair required`, `Version mismatch`, `Pairing expired`, and backend-switch blocked states.

#### Task 7.1. Reframe the page into state cards

Sub-tasks:

- Replace the long-form mixed layout with state-driven cards.
- Ensure each state exposes one primary next action.
- Keep the page useful for first pairing, reconnect, repair, version mismatch, and steady-state verification.

#### Task 7.2. Establish the default Chromium path

Sub-tasks:

- Keep Chromium-based browsers as the default pairing path.
- Make install/download guidance concise and state-aware.
- Avoid turning the default path into a browser-selection wizard.

#### Task 7.3. Add detection logic to funnel users through Firefox's support card when user agent is Firefox

Sub-tasks:

- Promote Firefox support into its own browser-specific support card.
- Keep the prerequisite sequence explicit: native enablement, Firefox setting, restart, then fresh pairing code.
- Avoid burying Firefox prerequisites inside the main pairing card.

#### Task 7.4. Separate connection state from recording preferences

Sub-tasks:

- Keep audio devices and other recording preferences distinct from install/pair/repair states.
- Ensure the page still reads as a connection-management surface first.
- Keep search and settings-navigation behavior intact after the layout changes.

#### Task 7.5. Normalize shared web copy

Sub-tasks:

- Replace duplicated local HTTPS and pairing error strings with shared, state-matrix-driven phrasing.
- Align state-card copy with native launcher, Settings, tray, and notifications.
- Keep the page concise even when multiple conditions such as version mismatch and repair requirements are present.

### Step 8. Align steady-state web surfaces with the new support model

Status: Complete.

This step should begin only after the web Companion page contract and copy are stable.

Implementation notes:

- Meeting Controls now use a state-driven support model so unpaired, temporarily disconnected, browser-repair, and version-mismatch states expose a clear next step instead of relying on disabled buttons and ad hoc status strings.
- The alert layer now treats `Browser repair required` as the only persistent Companion warning, while `Browser repair in progress`, `Version mismatch`, and `Temporarily disconnected` are reduced to transition-style informational or warning notices.
- The live waveform now distinguishes Companion preview failure from ordinary quiet audio so fetch failures no longer collapse into silent bars and trigger the wrong quiet-audio hint.
- Dashboard and Recordings tour copy now points users toward the Companion App settings page as the browser-side support surface for setup, reconnect, and repair guidance.

#### Task 8.1. Update Meeting Controls guidance

Sub-tasks:

- Make repair-required, disconnected, and unpaired states clearer.
- Ensure the primary next step is obvious from the disabled action surface.
- Keep the happy path lightweight once the Companion is healthy.

#### Task 8.2. Improve alert-layer behavior

Sub-tasks:

- Review persistent alerts versus page-level state cards.
- Remove redundant messaging where the same issue is already explained elsewhere.
- Keep `repairing` quiet and transient.

#### Task 8.3. Add a clearer degraded waveform state

Sub-tasks:

- Distinguish fetch failure from ordinary silence in the live waveform surface.
- Keep the behavior non-alarmist during transient issues.
- Ensure the waveform does not become the primary place where users learn about repair.

#### Task 8.4. Update tours and ancillary entry points

Sub-tasks:

- Refresh tour copy so it reflects the final Companion ownership model.
- Revisit download and Companion-status prompts outside Settings.
- Ensure ancillary entry points send users into the correct canonical surfaces.

### Step 9. Run the integrated validation pass and polish regressions

Status: Planned.

This step validates the assembled native and web experience before documentation is frozen.

#### Task 9.1. Execute the global validation matrix

Sub-tasks:

- Validate fresh Chromium install and pairing.
- Validate Firefox prerequisite flow and fresh-code pairing.
- Validate repair-required and repairing states.
- Validate replacement pairing, blocked backend switching, version mismatch, disconnected recovery, and tray recording fallback.

#### Task 9.2. Fix cross-surface regressions

Sub-tasks:

- Fix copy mismatches between native and web surfaces.
- Fix broken handoffs between launcher, Settings, pairing window, tray, and web UI.
- Fix any menu, focus, or notification regressions found during manual testing.

#### Task 9.3. Freeze the post-implementation UX contract

Sub-tasks:

- Confirm that no missing dependency work remains hidden in later documentation tasks.
- Confirm that the validation matrix still matches the implemented surfaces.
- Confirm that the final copy pack is stable enough to document.

### Step 10. Publish the dedicated Companion guide and update supporting docs

Status: Planned.

This step lands after the UI contract is stable so the docs can serve as a durable reference rather than a moving target.

#### Task 10.1. Create the dedicated end-user Companion guide

Sub-tasks:

- Add a dedicated Companion guide in the docs folder as the canonical user reference.
- Cover install, first launch, pairing, reconnect, re-pair, repair, tray usage, Chromium support, and Firefox support.
- Keep the guide user-facing rather than implementation-spec oriented.

#### Task 10.2. Update high-level onboarding docs

Sub-tasks:

- Shorten [GETTING_STARTED.md](GETTING_STARTED.md) so it points users into the dedicated Companion guide for detailed native setup.
- Update [USAGE.md](USAGE.md) so it references the guide for pairing, reconnect, repair, and browser support.
- Keep the general docs concise and non-duplicative.

#### Task 10.3. Update operator and developer docs

Sub-tasks:

- Update [DEVELOPMENT.md](DEVELOPMENT.md) for the new native UX, browser guidance, and validation expectations.
- Update [SECURITY.md](SECURITY.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [PRD.md](PRD.md) so the product description matches the implemented launcher, Settings, tray, and repair model.
- Ensure the docs describe the native-first ownership model accurately.

#### Task 10.4. Finalize verification guidance

Sub-tasks:

- Capture the final manual validation guidance that should accompany this UX upgrade.
- Ensure the dedicated Companion guide and supporting docs all reference the same support boundaries and browser expectations.
- Close the planning loop by keeping this document as the umbrella plan and decision record.

## Validation Matrix

- Fresh Windows install with Chrome or Edge: install, trust bootstrap, pair, and start a recording without needing extra troubleshooting.
- Fresh Windows install with Firefox: user finds the Firefox branch before failure, completes the explicit trust steps, restarts Firefox, and pairs successfully with a fresh code.
- Existing paired user with `localHttpsStatus=needs-repair`: native and web surfaces point to the same repair action and recover cleanly.
- Existing paired user with `localHttpsStatus=repairing`: the UI behaves as a quiet transient state without persistent alarm fatigue.
- Re-pair to a different backend while currently paired: user understands that the old backend stays active until the new pairing succeeds.
- Re-pair blocked by active recording or upload: both native and web surfaces explain why switching is blocked.
- Version mismatch after update: the user is told to update or re-pair with the least ambiguity possible.
- Companion temporarily disconnected but still paired: browser controls communicate that pairing is still valid and will resync automatically.
- Tray-menu usage during active recording or offline recovery: pause, stop, and recovery actions stay easy to find without burying them under low-frequency utility items.

## Expected Touch Points

- companion/src/index.html
- companion/src/settings.html
- companion/src/pairing.html
- companion/src-tauri/src/main.rs
- companion/src-tauri/tauri.conf.json
- frontend/src/components/settings/CompanionAppSettings.tsx
- frontend/src/components/settings/AudioSettings.tsx
- frontend/src/components/settings/SettingsPage.tsx
- frontend/src/components/MeetingControls.tsx
- frontend/src/components/LiveAudioWaveform.tsx
- frontend/src/components/ServiceStatusAlerts.tsx
- frontend/src/lib/serviceStatusStore.ts
- frontend/src/lib/companionLocalApi.ts
- frontend/src/lib/platform.ts
- frontend/src/lib/tour-config.ts
- docs/GETTING_STARTED.md
- docs/COMPANION.md
- docs/USAGE.md
- docs/DEVELOPMENT.md
- docs/SECURITY.md
- docs/ARCHITECTURE.md
- docs/PRD.md

## Waterfall Planning Decisions

### Decision 1. Canonical first-run owner

Status: Confirmed.

- Question: Which surface should own the first successful install and pair journey: the native Companion home window or the web Companion settings page?
- Decision: Native-first.
- Recommendation: Make the native Companion home window the primary first-run owner, and keep the web Companion page as the secondary confirmation, browser-branch, and recovery surface.
- Rationale: The local HTTPS bootstrap, repair authority, Firefox support action, and pairing initiation all originate in the Companion. Making the native surface primary reduces context switching before the browser has a dependable local control path and gives the waterfall plan a clear first implementation target.

### Decision 2. Native home window scope

Status: Confirmed.

- Question: Should the native home window become a full first-run wizard, or a lightweight task-oriented launcher that hands off detailed controls to Settings and the pairing window?
- Decision: Lightweight launcher.
- Recommendation: Keep the native home window lightweight and task-oriented. It should show state, one primary next action, and a small amount of first-run explanation, but it should not become a multi-step wizard or duplicate the full Settings surface.
- Rationale: The current code already has a dedicated Settings window, a dedicated pairing window, and tray entry points. A lightweight launcher preserves clear ownership boundaries, lowers implementation risk, and still satisfies the native-first decision without creating a second full configuration surface to maintain.

### Decision 3. Launcher auto-open policy

Status: Confirmed.

- Question: When should the lightweight launcher open automatically?
- Decision: Open it on explicit user launches, and also allow autostart to open it only when the machine starts with no active pairing state.
- Recommendation: Open it automatically on first explicit user launch and on later explicit launches when the Companion still needs blocking user action. Keep autostart quiet in normal paired states, but allow autostart to surface the launcher when the machine starts and the Companion still has no pairing.
- Rationale: The app remains tray-oriented during normal background use, but an unpaired autostart is effectively a broken setup state rather than ordinary background operation. This rule keeps normal autostart quiet without hiding the initial setup requirement indefinitely.

### Decision 4. Launcher action authority

Status: Confirmed.

- Question: Should the lightweight launcher directly trigger the high-frequency primary actions, or should it only route users into Settings?
- Decision: Direct primary actions from the launcher.
- Recommendation: Let the launcher directly trigger the primary happy-path action for the current state, but keep secondary and advanced actions in Settings.
- Rationale: If the launcher is native-first, forcing every user into Settings for the main next step weakens that choice. Directly triggering the single primary action keeps the launcher useful, while Settings still owns the broader configuration, recovery detail, and lower-frequency controls.

### Decision 5. Browser-branch ownership

Status: Confirmed.

- Question: Should the native launcher own browser-specific branching, especially the Firefox setup path, or should it stay browser-agnostic and defer that branch to Settings and the web UI?
- Decision: Browser-agnostic launcher.
- Recommendation: Keep the launcher browser-agnostic. Let it expose the generic primary action for the current state, and defer Firefox-specific branching, copy, and prerequisite handling to Settings and the web Companion page.
- Rationale: The current codebase only detects Firefox on the web side, while the native side exposes the privileged Firefox support action but has no browser context of its own. Pushing browser-specific branching into the launcher would either duplicate browser detection logic or add a new manual branch selector to the launcher, both of which would make the lightweight launcher heavier than intended.

### Decision 6. Launcher direct-action allowlist

Status: Confirmed.

- Question: Which direct actions should the lightweight launcher be allowed to trigger itself?
- Decision: Use a tight allowlist, but remove `Repair Local HTTPS` from direct launcher actions. The launcher may directly trigger `Start Pairing` or `Generate New Pairing Code`, `Open Nojoin`, and `Open Settings`. Configuration and troubleshooting actions, including `Repair Local HTTPS`, stay in Settings.
- Recommendation: Keep the launcher focused on forward progress and low-risk actions. Use direct launcher actions only for the main happy-path transition into pairing or web use. Route repair, Firefox support, disconnect, updates, logs, and autostart controls into Settings or the tray menu.
- Rationale: This preserves the launcher as a lightweight entry surface rather than a troubleshooting console. Pairing and web launch are simple, high-frequency actions. Repair is still important, but it belongs beside the richer context already present in Settings.

### Decision 7. Repair-required launcher behavior

Status: Confirmed.

- Question: When local HTTPS is in `needs-repair`, should the launcher expose a prominent `Open Settings to Repair` action, or should it demote the issue to passive status text and leave discovery to the tray and notifications?
- Decision: Expose a prominent repair-oriented CTA in the launcher, but make that CTA open Settings rather than triggering repair directly.
- Recommendation: Expose a prominent repair-oriented CTA in the launcher, but make that CTA open Settings rather than triggering repair directly.
- Rationale: You already chose native-first and removed direct repair from the launcher. That means the launcher still needs to act as the primary discovery surface for blocked secure-local states, otherwise users are forced back into tray hunting and alert interpretation.

### Decision 8. Healthy paired launcher behavior

Status: Confirmed.

- Question: When the Companion is already paired and healthy, what should the launcher primarily do on an explicit user launch?
- Decision: Show a compact healthy-state launcher with backend summary and a primary `Open Nojoin` CTA, plus a secondary `Open Settings` CTA.
- Recommendation: Show a compact healthy-state launcher with backend summary and a primary `Open Nojoin` CTA, plus a secondary `Open Settings` CTA.
- Rationale: The launcher should still justify opening, but it should not become another dashboard. In the healthy paired state, the user’s most likely intent is to get back to Nojoin quickly. A compact summary plus `Open Nojoin` keeps the launcher useful without adding unnecessary friction.

### Decision 9. Unpaired launcher behavior

Status: Confirmed.

- Question: When the Companion is unpaired but local HTTPS is healthy, should the launcher primarily start pairing immediately, or should it present a short explanation screen first and make pairing the next click?
- Decision: Present a short explanation screen with a primary `Start Pairing` CTA rather than immediately opening the pairing window on launcher open.
- Recommendation: Present a short explanation screen with a primary `Start Pairing` CTA rather than immediately opening the pairing window on launcher open.
- Rationale: Pairing is the primary next step, but opening the pairing window immediately would make the launcher feel skipped and remove the native-first onboarding context you chose earlier. A short explanation plus one clear CTA preserves user orientation without adding a real extra step.

### Decision 10. Tray menu top-level contract

Status: Confirmed.

- Question: Should the tray context menu top level be reduced to only operational items such as current status, active recording controls, `Open Nojoin`, `Settings`, and `Quit`, with low-frequency utilities removed from the primary surface?
- Decision: Yes. Reduce the top-level tray menu to status, active recording controls when relevant, `Open Nojoin`, `Settings`, and `Quit`. Move low-frequency utilities such as `Run on Startup`, `View Logs`, `About`, and manual update checks into Settings rather than leaving them in the primary tray surface.
- Recommendation: Yes. Reduce the top-level tray menu to status, active recording controls when relevant, `Open Nojoin`, `Settings`, and `Quit`. Move low-frequency utilities such as `Run on Startup`, `View Logs`, `About`, and manual update checks into Settings rather than leaving them in the primary tray surface.
- Rationale: You have already made the launcher and Settings the main native UX surfaces. That means the tray should become a fast operational fallback, especially during recording and offline recovery, not a secondary control panel with mixed-frequency items.

### Decision 11. Settings information architecture

Status: Confirmed.

- Question: Should the native Settings page separate the main happy path from troubleshooting and destructive actions by using explicit sections such as `Connection`, `Secure Local Browser Connection`, and `Advanced / Troubleshooting`?
- Decision: Yes. Keep pairing and current-state summary in the main path, keep local HTTPS state visible but route repair and Firefox support into a clearly marked troubleshooting section, and place `Disconnect Current Backend` in an explicit advanced/destructive section.
- Recommendation: Yes. Keep pairing and current-state summary in the main path, keep local HTTPS state visible but route repair and Firefox support into a clearly marked troubleshooting section, and place `Disconnect Current Backend` in an explicit advanced/destructive section.
- Rationale: The launcher and tray are now intentionally lightweight. That makes Settings the place where users should resolve edge cases safely. If troubleshooting and destructive actions remain visually co-equal with pairing and healthy-state actions, the UI will continue to feel dense and unfocused.

### Decision 12. Web Companion page interaction model

Status: Confirmed.

- Question: Should the web `Settings -> Companion App` page use a strict stepper-like progression, or should it use state cards with one primary CTA that adapts to the current Companion state?
- Decision: Use state cards with one primary CTA rather than a strict stepper.
- Recommendation: Use state cards with one primary CTA rather than a strict stepper.
- Rationale: The web Companion page is no longer the canonical first-run owner. It needs to work for first pairing, reconnect, repair, Firefox guidance, updates, and steady-state verification. Those are not a single linear journey, so a stepper would add rigidity and feel wrong once the user returns to the page outside the happy path.

### Decision 13. Firefox web-flow presentation

Status: Confirmed.

- Question: In the web Companion page, should Firefox stay as a secondary branch inside the main pairing card, or should it be promoted into a clearly separate browser-specific support card?
- Decision: Promote Firefox into a clearly separate support card, while keeping Chromium-based browsers as the default pairing path.
- Recommendation: Promote Firefox into a clearly separate support card, while keeping Chromium-based browsers as the default pairing path.
- Rationale: Firefox is not just another pairing mode toggle. It has a materially different prerequisite path involving native support enablement, Firefox configuration, restart, and a fresh pairing code. Keeping it inside the same main pairing card risks burying those requirements and making the default path feel heavier for the majority case.

### Decision 14. End-user documentation shape

Status: Confirmed.

- Question: Should Companion onboarding, pairing, repair, and browser-support guidance live in a dedicated end-user Companion guide, or should it be absorbed into [GETTING_STARTED.md](GETTING_STARTED.md) and [USAGE.md](USAGE.md)?
- Decision: Create a dedicated Companion guide, then keep [GETTING_STARTED.md](GETTING_STARTED.md) and [USAGE.md](USAGE.md) concise and link into it.
- Recommendation: Create a dedicated Companion guide, then keep [GETTING_STARTED.md](GETTING_STARTED.md) and [USAGE.md](USAGE.md) concise and link into it.
- Rationale: The Companion now has enough state-specific behavior, launcher logic, repair flows, browser branching, and tray fallback behavior that scattering this guidance across general docs will increase duplication and drift. A dedicated guide gives users one canonical reference while the broader docs stay focused.

## Open Questions For Review

None for this pass. The current decision set is now complete enough to translate into an ordered waterfall implementation plan.
