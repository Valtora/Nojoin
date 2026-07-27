# Nojoin Administration Guide

This guide is for Owners and Admins running a self-hosted Nojoin installation.

## Roles

Nojoin supports three primary roles:

- **Owner**: Full system access, including server configuration and user management.
- **Admin**: Can manage users and invitations, but cannot take the highest-privilege owner actions.
- **User**: Standard access to personal recordings, settings, and connected services.

Additional privilege guardrails apply around Owner creation and superuser-only operations.

## Invitations

Registration is invite-gated.

Admins can:

- Create invitation links.
- Choose the invited role between `user` and `admin`.
- Set expiry and usage limits.
- Revoke invites while retaining the historical record.
- Delete revoked invites permanently if desired.

Users who register through an invite choose their own password during sign-up and are not forced through an immediate password-rotation flow.

## Manual User Provisioning and Password Rotation

New and rotated passwords are enforced server-side.

- Passwords must be at least 8 characters long.
- Passwords made entirely of whitespace are rejected.
- Existing password hashes are grandfathered until the next password change or admin reset.

When an Admin or Owner creates a user manually:

- The user receives a temporary password.
- The user must choose a new password before the rest of the application becomes available.
- While `force_password_change` is active, Nojoin only allows self-profile access, password update, and logout.

The same restriction also applies when a superuser resets another user's password through the privileged user-management flow.

## Administration Settings Areas

### Calendar

Use **Settings > Administration > Calendar providers** to save installation-wide Google and Microsoft OAuth credentials.

Read [CALENDAR.md](CALENDAR.md) for the full provider registration and tenant guidance.

### AI and Models

Use **Settings > AI** for installation-wide provider defaults, model operations, and Ollama configuration.

> [!IMPORTANT]
> For security, LLM provider API keys and Hugging Face tokens are server-side environment-only variables and must be configured via environment variables (e.g., `.env`) and the container restarted, rather than through UI settings fields.

Admin-only sections let you:

- Choose the default LLM provider.
- Configure the Ollama API URL and context window. The context window is sent to Ollama as `num_ctx` for full-context meeting prompts; if Ollama still reports a length stop, Nojoin surfaces that as a chat error instead of saving a truncated answer.
- Configure a secondary LLM provider for fallback. When the primary provider fails, the system automatically retries with the secondary provider. The secondary provider has its own independent configuration (provider, model, API key, Ollama URL) set through `SECONDARY_` prefixed environment variables.
- View installed Whisper models.
- Remove local model cache entries. Required default models are prepared on first run, and repo-bundled model assets remain read-only in the UI. Deletion runs on a worker, since the API mounts the model cache read-only, so it needs a running worker to succeed.
- Choose when a newly selected transcription model is downloaded. Picking a model that is not on the server prompts you to fetch it now, so it is ready before the next recording, or to leave it until first use. Declining is safe: the model is downloaded when it is first needed, but live transcription and Meeting Edge wait for that download to finish.
- Download any model listed as **Missing** in **Model dependencies**, with progress shown in place. One preparation runs at a time, so the buttons are disabled while another is in flight.

**Notes structure and Glossary** are also administered from **Settings > AI**. Both are two-tier: an install value you maintain for everyone, plus a per-user value.

- Install structures are visible to every user and editable only by an administrator. One can be marked the install default, which applies to anyone who has not chosen their own. Users can copy an install structure into their own list to vary it, which is the intended route when someone wants a change to shared text.
- The install glossary is merged into each user's own glossary rather than replacing it, so a user adding one personal term keeps the organisation's vocabulary. Where both define the same term, the user's definition is used.
- Only the section structure is editable. Accuracy, attribution, table formatting, and the response contract are fixed by the application and cannot be removed by a template. See [ADR-0006](adr/0006-user-editable-notes-structure.md) for the boundary and why it sits there.
- A template's structure text is snapshotted onto each set of notes it generates, so editing or deleting a template never changes what past notes were produced from. Restores remap template references; a reference that cannot be remapped is dropped rather than pointed at an unrelated template.

Each user can also configure **Language preferences** in **Settings > AI**. Transcription defaults to automatic language detection, while generated meeting titles and notes default to English. Whisper supports forced language selection, Canary supports its listed language set, and Parakeet remains automatic-only. The selected effective transcription language is part of the ASR reuse key, so cached/live transcript work is not reused across incompatible language settings.

Notes-language choices include British and American English, the transcription language, listed languages, and a validated custom language/style instruction. These choices localize generated content while preserving machine-readable JSON keys and speaker labels. Existing saved transcripts and notes are not translated in place; users must reprocess or run **Generate Notes** after changing the relevant preference.

### Anonymous Usage Data

Use **Settings > Administration > Anonymous usage data** to turn the daily anonymous ping on or off, and to see this install's random ID, the endpoint, and when a ping was last sent. The panel also lists exactly what the ping contains.

On an installation upgraded into this feature, nothing is sent until an administrator has seen the one-time notice; the panel says so explicitly while that is the case. If `NOJOIN_TELEMETRY_ENABLED` is set in the environment, the toggle is read-only and the panel explains why.

Read [TELEMETRY.md](TELEMETRY.md) for the full disclosure, the retention policy, and how to verify what is sent.

### Backup and Restore

Use **Settings > Administration > Backup and restore** for export and restore operations.

Read [BACKUP_RESTORE.md](BACKUP_RESTORE.md) before relying on it operationally, especially because backup archives can contain restorable calendar credentials and connected-account tokens.

### System

Use **Settings > Administration > System operations** for operational controls such as:

- Restarting the stack.
- Viewing live logs.
- Filtering merged or per-service log output.
- Downloading log output for investigation.

### Updates

Use **Settings > Updates** to see:

- The installed server version from the current API build.
- The latest stable published release.
- Release history and release notes.

## Operational Notes

- Back up the installation before upgrading.
- Review release notes for browser capture, auth, and upload lifecycle changes.
- After live-pipeline upgrades, use the recording page's waveform, Meeting Edge status, and overall recording progress before treating a meeting as stuck.
- For remote deployments, configure a trusted public origin with `WEB_APP_URL`.
- Treat backup archives as sensitive material.

### Browser Capture Support

- Shared-audio live recording requires Chrome on Windows, Linux, or macOS, or another supported desktop Chromium browser. Other Chromium-family browsers on macOS are best-effort.
- Chrome on Android and iOS can start microphone-only live recordings.
- Firefox, Safari, and other mobile browsers can review and administer Nojoin but cannot start live capture.
- Tab sharing with audio enabled is the recommended support path for browser-based meetings.
- If local microphone audio is missing, ask the user to grant microphone permission and review **Settings > Capture**.
- If remote participant audio is missing, ask the user to start again and enable shared audio in the browser picker.
- If a mobile Chrome recording is missing remote participants, confirm the user expected microphone-only capture and that the phone microphone could hear the meeting audio.
- If a user has a paused recording, they must resume or discard it before starting another capture.
- Review backend and worker logs for segment upload, transcode, live transcription, finalize, or discard failures.
- Browser-live segment numbering starts at `0`; upload or finalize support cases should confirm the sequence is contiguous.
- The worker keeps browser-live audio as 16 kHz, two-channel WAV after transcode. Channel 0 is shared/system audio when available and channel 1 is microphone audio.

Paused recordings are retained indefinitely and are not cleaned up automatically. This protects uploaded meeting data and prevents overlapping segment streams for the same user.

Read [CAPTURE.md](CAPTURE.md) for the support matrix, browser picker behaviour, and troubleshooting steps.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md)
- [CALENDAR.md](CALENDAR.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [CAPTURE.md](CAPTURE.md)
- [TELEMETRY.md](TELEMETRY.md)
- [USAGE.md](USAGE.md)
