---
name: Platform Compatibility Report
about: Report a browser-capture issue specific to an OS, browser, or device
title: '[Platform]: Browser capture issue on <Browser / OS>'
labels: 'platform-issue, help wanted, needs-triage'
assignees: ''

---

**Operating system / device**
 - Platform and version: [e.g. Windows 11 23H2, Ubuntu 24.04, macOS 15, Android 14, iOS 17]
 - Desktop environment (Linux only): [e.g. GNOME on Wayland, KDE on X11]

**Browser**
 - Browser and version: [e.g. Chrome 137.0.0.0, Edge, Brave]

**Capture mode**
 - [ ] Desktop shared-audio (tab, window or entire screen)
 - [ ] Mobile microphone-only (Chrome on Android or iOS)

Supported: Chrome on Windows, Linux and macOS, and other Chromium-family browsers on Windows and Linux, for shared-audio recording; other Chromium-family browsers on macOS as best-effort; Chrome on Android and iOS for microphone-only recording. See [CAPTURE.md](https://github.com/Valtora/Nojoin/blob/main/docs/CAPTURE.md).

**Nojoin version**
e.g. 2.10.0, shown under **Settings > Updates**.

**Describe the issue**
What happened.

**To reproduce**
1. Open Nojoin in the browser above.
2. Click **Start Meeting** on the **Meet Now** card, or run the microphone test under **Settings > Recording**.
3. Share a tab, window or screen with audio (desktop), or grant microphone access (mobile).
4. See the issue.

**Expected behaviour**
What you expected to happen.

**Logs**
Redact personal data first.
- Browser: copy relevant console or network errors from DevTools.
- Server: `docker compose logs api worker-gpu worker-cpu worker-io worker-parse frontend`.

**Additional context**
Anything else, including whether the share picker offered an audio toggle and whether a shared-audio track was granted.
