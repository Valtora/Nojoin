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
- Choose the invited role.
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

Use **Settings > AI** for installation-wide provider defaults, credentials, and model operations. Admin-only sections there let you:

- Choose the default LLM provider.
- Store provider API keys.
- Configure Ollama.
- View installed Whisper models.
- Download or remove local Whisper models.

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

- Shared-audio live recording requires a supported Chromium browser on Windows or Linux.
- Chrome on Android and iOS can start microphone-only live recordings.
- Firefox, Safari, other mobile browsers, and Chromium browsers on macOS can review and administer Nojoin but cannot start live capture.
- Tab sharing with audio enabled is the recommended support path for browser-based meetings.
- If local microphone audio is missing, ask the user to grant microphone permission and review **Settings > Capture**.
- If remote participant audio is missing, ask the user to start again and enable shared audio in the browser picker.
- If a mobile Chrome recording is missing remote participants, confirm the user expected microphone-only capture and that the phone microphone could hear the meeting audio.
- If a user has a paused recording, they must resume or discard it before starting another capture.
- Review backend and worker logs for segment upload, transcode, live transcription, finalize, or discard failures.
- Browser-live segment numbering starts at `0`; upload or finalize support cases should confirm the sequence is contiguous.
- The worker keeps browser-live audio as 16 kHz, two-channel WAV after transcode. Channel 0 is shared/system audio when available and channel 1 is microphone audio.

Paused recordings are retained indefinitely and are not cleaned up automatically. This protects uploaded meeting data and prevents overlapping segment streams for the same user.

Read [CAPTURE.md](CAPTURE.md) for the support matrix, browser picker behavior, and troubleshooting steps.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md)
- [CALENDAR.md](CALENDAR.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [CAPTURE.md](CAPTURE.md)
- [USAGE.md](USAGE.md)
