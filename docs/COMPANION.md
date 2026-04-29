# Nojoin Companion Guide

This guide is the canonical end-user reference for the Windows Nojoin Companion app.

Use it for install, first launch, pairing, reconnect, re-pair, repair, tray usage, and browser-specific setup.

For first deployment of Nojoin itself, start with [GETTING_STARTED.md](GETTING_STARTED.md). For day-to-day product use after setup, see [USAGE.md](USAGE.md).

## What the Companion Does

- Captures system audio and microphone audio locally on Windows.
- Pairs this machine to one Nojoin deployment at a time.
- Keeps pairing, repair, Firefox support, updates, logs, and disconnect actions inside the native app.
- Works best with Chrome or Edge. Firefox is supported, but it requires extra setup.

## Which Surface To Use

| Surface | Use it for |
| --- | --- |
| Launcher | First launch, the primary next step, and a quick route into Nojoin or Settings |
| Settings | Pairing, re-pairing, repair, Firefox support, updates, logs, run-on-startup, and disconnect |
| Pairing window | The current 8-character pairing code, countdown, copy action, and `Cancel Pairing` |
| Tray | Quick status, recording controls, `Open Nojoin`, `Settings`, and `Quit` |
| Nojoin web Companion page | Browser-side status checks and code entry after the native app has already started pairing |

The browser can confirm state and guide you back to the right native action, but native-only actions still happen in the Companion app.

## Before You Start

- The Companion currently supports Windows only.
- The Companion pairs to one Nojoin deployment at a time.
- Chrome or Edge is the default path for local browser control.
- Firefox requires explicit setup before pairing or local browser control will work correctly.
- If you switch this machine to a different Nojoin deployment later, use `Generate New Pairing Code` rather than assuming the previous pairing is already gone.

## Install Or Update The Companion

1. Download the latest Windows Companion build from GitHub Releases or from Nojoin `Settings -> Updates`.
2. Install the app or run the portable build.
3. Launch the Companion.
4. On first run, the launcher opens with the next step. If the app is already paired and healthy, it may stay in the tray until you open it.

When the Companion is auto-launched at Windows sign-in (the `Run on Startup` setting), it always stays in the system tray and never opens a window in the foreground, regardless of pairing state. If the machine is not yet paired, a one-time tray notification reminds you that the Companion is running; right-click the tray icon and choose `Settings` or `Open Companion` to finish setup.

If an update clears old trust state, expect to pair again after updating.

## First Pair On Chrome Or Edge

1. Open the Companion.
2. If the launcher shows `Set up Companion` or `Not paired`, choose `Start Pairing`.
3. A pairing window opens with an 8-character code and countdown.
4. Open your Nojoin site and go to `Settings -> Companion App`.
5. Enter the current code in the web pairing form.
6. When pairing succeeds, the pairing window closes and the Companion status becomes `Connected`.
7. Return to the dashboard and start your recording.

If you are not ready to pair yet, you can close the launcher and come back later from the tray.

## Firefox Support

Firefox is supported, but it is not the default path.

If you want to use Firefox on this Windows machine:

1. Open Companion `Settings`.
2. Choose `Enable Firefox Support`.
3. In Firefox, open `about:config` and set `security.enterprise_roots.enabled` to `true`.
4. Restart Firefox.
5. Back in the Companion, choose `Generate New Pairing Code`.
6. Open Nojoin in Firefox and enter the fresh code on `Settings -> Companion App`.

If the web app shows `Firefox setup incomplete`, do not keep retrying the old code. Finish the steps above, restart Firefox, and use a fresh code.

## Common Companion States

| Status | What it means | What to do next |
| --- | --- | --- |
| `Connected` | The Companion is paired and ready. | Use `Open Nojoin` or start from the dashboard. |
| `Not paired` | This machine does not have an active backend pairing. | Choose `Start Pairing` from the launcher or `Settings`. |
| `Pairing code active` | A valid local pairing code is waiting in the pairing window. | Finish pairing in Nojoin with the current code. |
| `Temporarily disconnected` | Pairing is still valid, but the browser cannot reach the local Companion right now. | Wait a moment first. If it does not recover, open `Settings`. |
| `Browser repair in progress` | The Companion is repairing local browser access. | Keep `Settings` open and wait for automatic refresh. |
| `Browser repair required` | Local browser control is blocked until native repair runs. | Use `Open Settings to Repair`, then run `Repair Local Browser Connection` in the Companion. |
| `Version mismatch` | The Nojoin site and Companion are no longer on a compatible build pair. | Update the older side first. If trust is cleared, pair again with a fresh code. |
| `Firefox setup incomplete` | Firefox prerequisites were not finished. | Run `Enable Firefox Support`, enable enterprise roots in Firefox, restart Firefox, and use a fresh code. |
| `Pairing expired` | The current code is no longer valid. | Choose `Generate New Pairing Code`. |

## Reconnect, Re-pair, And Switch Deployments

- `Temporarily disconnected` is not the same as unpaired. Your pairing is still valid and should recover automatically.
- To move this machine to a different Nojoin deployment, open Companion `Settings` and choose `Generate New Pairing Code`.
- The current backend stays active until the new pairing succeeds.
- If a recording is still active or an upload is still finishing, backend switching stays blocked until that work is done.
- Use `Disconnect Current Backend` only when you intentionally want to remove the current pairing and return the app to an unpaired state.

## Repair The Local Browser Connection

Repair runs only in the native Companion app.

1. Open Companion `Settings`.
2. If Nojoin or the launcher tells you `Browser repair required`, choose `Open Settings to Repair`.
3. In `Settings`, run `Repair Local Browser Connection`.
4. Wait while the status shows `Browser repair in progress`.
5. Retry the original browser action after the Companion returns to `Connected`.

Do not expect the web app to run repair for you. The web side can only tell you when to go back to the Companion.

## Using The Tray

- The tray is the quick operational fallback surface.
- It shows the current status, active recording controls when needed, `Open Nojoin`, `Settings`, and `Quit`.
- During recording, the tray keeps pause, resume, and stop close at hand if the browser is not convenient.
- Double-click opens Nojoin when the Companion is paired. If it is not paired, double-click focuses the native onboarding surface instead.
- Low-frequency actions such as updates, logs, run-on-startup, Firefox support, repair, and disconnect live in `Settings`, not in the tray menu.

## Logs

- The Companion writes to `nojoin-companion.log` inside the per-user app data directory (`%APPDATA%\Nojoin\` on Windows, `~/.local/share/nojoin/` on Linux, `~/Library/Application Support/nojoin/` on macOS).
- The active log is rotated when it exceeds 5 MiB. The five most recent rotations are kept as `nojoin-companion.log.1` through `nojoin-companion.log.5`; older rotations are deleted automatically.
- On Unix the log files are created with mode `0600` and re-tightened on every startup. On Windows the per-user `%APPDATA%` ACL is relied on. Do not relax these permissions; the file may contain operational metadata for paired backends.
- The log level is fixed at `info`. Network-stack targets (`reqwest`, `hyper`, `h2`, `rustls`, `tokio_rustls`, `tower`, `axum`) are filtered to `warn` and above so request bodies and headers cannot leak even if a future contributor enables verbose logging.
- Bearer tokens, JWT-shaped strings, JSON values for known sensitive keys, and long opaque base64url runs are redacted before any HTTP error body or panic message is written. Treat the redacted file as the canonical artefact to share when reporting issues.

## Quick Troubleshooting

- The code expired: choose `Generate New Pairing Code` and use the new code immediately.
- Firefox still cannot pair: confirm `Enable Firefox Support`, confirm `security.enterprise_roots.enabled`, restart Firefox, then use a fresh code.
- The Companion says `Temporarily disconnected`: wait briefly before assuming repair is required.
- The web app says `Browser repair required`: open the Companion and run `Repair Local Browser Connection` from `Settings`.
- You are switching to a different backend: do not expect the old pairing to disappear until the new pairing succeeds.
- A recording or upload is still in progress: finish or wait before trying to replace the backend pairing.
- The log shows `Recovering from poisoned <name> mutex.`: the Companion intentionally recovers from internal panics rather than tearing down the loopback HTTPS listener mid-pairing. Pairing and local control continue to work; share the surrounding log lines (rotated files included) when reporting the issue so the originating panic can be traced.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [USAGE.md](USAGE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [SECURITY.md](SECURITY.md)
