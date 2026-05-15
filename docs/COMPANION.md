# Nojoin Companion Guide

This guide is the canonical end-user reference for the Windows Nojoin Companion app.

Use it for install, first launch, pairing, reconnect, switching deployments, tray usage, and troubleshooting.

For first deployment of Nojoin itself, start with [GETTING_STARTED.md](GETTING_STARTED.md). For day-to-day product use after setup, see [USAGE.md](USAGE.md).

## What the Companion Does

- Captures system audio and microphone audio locally on Windows.
- Pairs this machine to one Nojoin deployment at a time.
- Receives browser-initiated pairing requests and asks for OS-native approval on this device.
- Keeps updates, logs, run-on-startup, and disconnect actions inside the native app.
- Exposes local status and recording controls to the Nojoin web client after pairing succeeds.

## Which Surface To Use

| Surface | Use it for |
| --- | --- |
| Launcher | First launch, the primary next step, and a quick route into Nojoin or Settings |
| Settings | Status, run-on-startup, logs, updates, and disconnect |
| Tray | Quick status, recording controls, `Open Nojoin`, `Settings`, and `Quit` |
| Nojoin web Companion page | Start pairing requests, monitor pairing state, and review browser-side Companion status |

The browser can start pairing, but approval still happens in the local Companion app through an OS-native prompt.

## Before You Start

- The Companion currently supports Windows only.
- The browser and Companion app must run on the same machine.
- The Nojoin backend can be local, remote, across a LAN, or across a VPN as long as both the browser and the Companion can reach the same HTTPS origin.
- The Companion pairs to one Nojoin deployment at a time.
- No pairing code fallback exists. Each request is signed by the backend and must be explicitly approved on this device.

## Install Or Update The Companion

1. Download the latest Windows Companion build from GitHub Releases or from Nojoin `Settings -> Updates`.
2. Install the app or run the portable build.
3. Launch the Companion.
4. On first run, the launcher opens with the next step. If the app is already paired and healthy, it may stay in the tray until you open it.

If you use the portable build, launch Companion once before starting pairing so Windows can register the `nojoin://` handler for browser handoff.

When the Companion is auto-launched at Windows sign-in (the `Run on Startup` setting), it always stays in the system tray and never opens a window in the foreground, regardless of pairing state. If the machine is not yet paired, a one-time tray notification reminds you that the Companion is running; right-click the tray icon and choose `Settings` or `Open Companion` to finish setup.

If an update clears old trust state, expect to pair again after updating.

## First Pair Or Refresh A Pairing

1. Open the Companion.
2. Open your Nojoin site and go to `Settings -> Companion`.
3. Choose `Pair This Device`. The browser creates a signed pairing request and opens the local Companion through `nojoin://pair`.
4. Review the OS-native prompt that names the Nojoin deployment and username. Approve to continue or decline to cancel.
5. Keep the browser page open while the request moves through `Waiting for Companion`, `Approval pending`, and `Completing pairing`.
6. When pairing succeeds, the Companion status becomes `Connected` and the web page refreshes automatically.
7. Return to the dashboard and start your recording.

If Windows says no app is associated with `nojoin://`, or nothing appears after you start the request, relaunch Companion and then start a fresh request from the browser.

## Reconnect And Switch Deployments

- `Temporarily disconnected` is not the same as unpaired. Your pairing is still valid and should usually recover automatically.
- To move this machine to a different Nojoin deployment, start a fresh pairing request from the target Nojoin site in the browser.
- The current backend stays active until the new pairing request succeeds.
- If a recording is still active or an upload is still finishing, backend switching stays blocked until that work is done.
- Use `Disconnect Current Backend` only when you intentionally want to remove the current pairing and return the app to an unpaired state.

## Common Companion States

| Status | What it means | What to do next |
| --- | --- | --- |
| `Connected` | The Companion is paired and ready. | Use `Open Nojoin` or start from the dashboard. |
| `Not paired` | This machine does not have an active backend pairing. | Open Nojoin and start pairing from `Settings -> Companion`. |
| `Approval pending` | The Companion is waiting for an OS-native accept or decline decision. | Approve or decline the native prompt on this device. |
| `Completing pairing` | The local approval succeeded and the Companion is finishing secure backend registration. | Keep the browser page open until the page refreshes into the connected state. |
| `Temporarily disconnected` | Pairing is still valid, but the browser cannot reach the local Companion right now. | Wait a moment first. If it does not recover, open `Settings`. |
| `Local browser connection recovering` | The Companion is restoring its local browser connection automatically. | Wait for the connection to settle, then retry from the browser. |
| `Local browser connection unavailable` | Browser-side local controls are unavailable on this device right now. | Quit and relaunch Companion, then retry the browser action. |
| `Version mismatch` | The Nojoin site and Companion are no longer on a compatible build pair. | Update the older side first. If trust is cleared, pair again from the browser after versions align. |

## If Browser-Side Local Control Stops Working

1. Wait briefly if the status is `Temporarily disconnected` or `Local browser connection recovering`.
2. If the status becomes `Local browser connection unavailable`, quit and relaunch Companion.
3. Retry the original browser action after the Companion settles.
4. If the state still does not recover, open `Settings` to review status, align versions if needed, or intentionally disconnect and start a fresh pairing request.

The web app can show state and start signed pairing requests, but it cannot repair the local connection or disconnect the backend on its own.

## Using The Tray

- The tray is the quick operational fallback surface.
- It shows the current status, active recording controls when needed, `Open Nojoin`, `Settings`, and `Quit`.
- During recording, the tray keeps pause, resume, and stop close at hand if the browser is not convenient.
- Double-click opens Nojoin when the Companion is paired. If it is not paired, double-click focuses the native onboarding surface instead.
- Low-frequency actions such as updates, logs, run-on-startup, and disconnect live in `Settings`, not in the tray menu.

## Logs

- The Companion writes to `nojoin-companion.log` inside the per-user app data directory (`%APPDATA%\Nojoin\` on Windows, `~/.local/share/nojoin/` on Linux, `~/Library/Application Support/nojoin/` on macOS).
- The active log is rotated when it exceeds 5 MiB. The five most recent rotations are kept as `nojoin-companion.log.1` through `nojoin-companion.log.5`; older rotations are deleted automatically.
- On Unix the log files are created with mode `0600` and re-tightened on every startup. On Windows the per-user `%APPDATA%` ACL is relied on. Do not relax these permissions; the file may contain operational metadata for paired backends.
- The log level is fixed at `info`. Network-stack targets (`reqwest`, `hyper`, `h2`, `rustls`, `tokio_rustls`, `tower`, `axum`) are filtered to `warn` and above so request bodies and headers cannot leak even if a future contributor enables verbose logging.
- Bearer tokens, JWT-shaped strings, JSON values for known sensitive keys, and long opaque base64url runs are redacted before any HTTP error body or panic message is written. Treat the redacted file as the canonical artefact to share when reporting issues.

## Quick Troubleshooting

- The browser says no app is associated with `nojoin://`: launch or relaunch Companion, then start a fresh pairing request.
- The pairing request expired or was declined: start a fresh request from the browser and approve it promptly.
- The Companion says `Temporarily disconnected`: wait briefly before assuming the pairing is gone.
- The web app says `Local browser connection unavailable`: relaunch Companion and retry the browser action.
- Firefox on Windows reaches the Nojoin site but cannot reach the local Companion while Chrome works: in the Companion app open `Settings`, expand `Advanced`, choose `Enable Firefox Support`, approve the Windows administrator prompt, confirm `about:config -> security.enterprise_roots.enabled` is `true`, restart Firefox, then start a fresh pairing request.
- You are switching to a different backend: do not expect the old pairing to disappear until the new pairing succeeds.
- A recording or upload is still in progress: finish or wait before trying to replace the backend pairing.
- The browser and Companion are on different machines: pairing will not work. The backend can be remote, but the browser and local Companion must be on the same device.
- The log shows `Recovering from poisoned <name> mutex.`: the Companion intentionally recovers from internal panics rather than tearing down the loopback HTTPS listener mid-pairing. Pairing and local control continue to work; share the surrounding log lines (rotated files included) when reporting the issue so the originating panic can be traced.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [USAGE.md](USAGE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [SECURITY.md](SECURITY.md)
