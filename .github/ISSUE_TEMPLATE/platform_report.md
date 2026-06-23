---
name: Platform Compatibility Report
about: Report a browser-capture issue specific to an OS, browser, or device
title: '[Platform]: Browser capture issue on <Browser / OS>'
labels: 'platform-issue, help wanted'
assignees: ''

---

**Operating system / device**
 - Platform: [e.g. Windows 11, Ubuntu 22.04, macOS Sequoia, Android 14, iOS 17]
 - Version: [e.g. 23H2, 22.04.3 LTS, 15.0]
 - Desktop environment (Linux only): [e.g. GNOME, KDE, X11 or Wayland]

**Browser**
 - Browser: [e.g. Chrome, Edge, Brave, Chromium]
 - Version: [e.g. 137.0.0.0]

**Capture mode**
 - [ ] Desktop shared-audio (tab / window / entire screen)
 - [ ] Mobile microphone-only (Chrome on Android or iOS)

Supported targets for reference (see [docs/CAPTURE.md](../../docs/CAPTURE.md)):
- Chrome on Windows, Linux, and macOS — shared-audio recording.
- Other Chromium-family browsers on Windows and Linux — shared-audio recording.
- Chromium-family browsers on macOS — best-effort shared-audio recording.
- Chrome on Android and iOS — microphone-only recording.

**Nojoin version**
 - Version: [e.g. v1.3.8]

**Describe the issue**
A clear and concise description of what happened.

**To reproduce**
Steps to reproduce the behaviour:
1. Open Nojoin in the browser above
2. Start Meet Now or open Settings -> Capture
3. Share a tab/window/screen with audio (desktop) or grant microphone access (mobile)
4. See the issue

**Expected behaviour**
A clear and concise description of what you expected to happen.

**Logs**
Please attach any relevant logs, with personal data redacted.
- Browser: open DevTools and copy any relevant console or network errors.
- Server: collect `docker compose logs api worker frontend`.

**Additional context**
Add any other context about the problem here, including whether the browser share picker exposed an audio toggle and whether a shared-audio track was granted.
