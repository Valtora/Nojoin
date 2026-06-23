---
name: Bug report
about: Report a problem to help us improve Nojoin
title: ''
labels: bug
assignees: ''

---

**Describe the bug**
A clear and concise description of what the bug is. For an intermittent test failure, prefix the title with `[flaky]` and include the test name and how often it fails.

**Nojoin version**
e.g. v1.3.8 (see `docs/VERSION` or the about/footer in the app).

**Deployment mode**
 - [ ] Docker Compose (recommended deployment)
 - [ ] Local source / development checkout
 - GPU or CPU: [e.g. NVIDIA GPU, CPU-only]

**Browser and capture mode** (if the issue involves recording)
 - Browser and version: [e.g. Chrome 137 on Windows 11]
 - Capture mode: [e.g. desktop shared-audio, mobile microphone-only]

**To reproduce**
Steps to reproduce the behaviour:
1. Go to '...'
2. Click on '...'
3. See the error

**Expected behaviour**
A clear and concise description of what you expected to happen.

**Reproduction data**
If the issue depends on specific input (an audio file, a recording length, a calendar event, an imported file), describe it. Do not upload confidential recordings or transcripts; provide a minimal, non-sensitive sample where possible.

**Logs**
Attach relevant logs. **Redact secrets and personal data first** — remove `FIRST_RUN_PASSWORD`, `DATA_ENCRYPTION_KEY`, API keys, tokens, `Authorization` headers, cookies, and meeting content you do not want public.
- Browser: open DevTools and copy any relevant console or network errors.
- Server: collect `docker compose logs api worker frontend`.

**Screenshots**
If applicable, add screenshots to help explain the problem (with sensitive content redacted).

**Additional context**
Add any other context about the problem here.
