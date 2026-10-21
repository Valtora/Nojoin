---
name: Bug report
about: Report a problem to help us improve Nojoin
title: ''
labels: 'bug, needs-triage'
assignees: ''

---

**Describe the bug**
What happened. For an intermittent test failure, prefix the title with `[flaky]` and give the test name and how often it fails.

**Nojoin version**
e.g. 2.10.0, shown under **Settings > Updates**.

**Deployment mode**
 - [ ] Docker Compose
 - [ ] Local source checkout
 - GPU or CPU: [e.g. NVIDIA GPU, CPU-only]

**Browser and capture mode** (if the issue involves recording)
 - Browser and version: [e.g. Chrome 137 on Windows 11]
 - Capture mode: [e.g. desktop shared-audio, mobile microphone-only]

**To reproduce**
1. Go to '...'
2. Click on '...'
3. See the error

**Expected behaviour**
What you expected to happen.

**Reproduction data**
If the issue depends on specific input (an audio file, a recording length, a calendar event, an imported file), describe it. Do not upload confidential recordings or transcripts; share a small non-sensitive sample if you can.

**Logs**
**Redact secrets and personal data first:** `FIRST_RUN_PASSWORD`, `DATA_ENCRYPTION_KEY`, API keys, tokens, `Authorization` headers, cookies, and any meeting content you do not want public.
- Browser: copy relevant console or network errors from DevTools.
- Server: `docker compose logs api worker-gpu worker-cpu worker-io worker-parse frontend`. An admin can also download each service's log under **Settings > System and logs**.

**Screenshots**
If they help, with sensitive content redacted.

**Additional context**
Anything else about the problem.
