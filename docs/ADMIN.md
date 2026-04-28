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

When an Admin or Owner creates a user manually:

- The user receives a temporary password.
- The user must choose a new password before the rest of the application becomes available.
- While `force_password_change` is active, Nojoin only allows self-profile access, password update, and logout.

The same restriction also applies when a superuser resets another user's password through the privileged user-management flow.

## Admin Settings Areas

### Calendar

Use **Settings > Admin > Calendar** to save installation-wide Google and Microsoft OAuth credentials.

Read [CALENDAR.md](CALENDAR.md) for the full provider registration and tenant guidance.

### AI and Models

Use the AI settings area to:

- Choose the default LLM provider.
- Store provider API keys.
- Configure Ollama.
- View installed Whisper models.
- Download or remove local Whisper models.

### Backup and Restore

Use **Settings > Backup & Restore** for export and restore operations.

Read [BACKUP_RESTORE.md](BACKUP_RESTORE.md) before relying on it operationally, especially because backup archives can contain restorable calendar credentials and connected-account tokens.

### System

Use **Settings > System** for operational controls such as:

- Restarting the stack.
- Viewing live logs.
- Filtering merged or per-service log output.
- Downloading log output for investigation.

### Updates

Use **Settings > Updates** to see:

- The installed server version from the current API build.
- The latest stable published release.
- Release history and release notes.
- Companion installer links.

## Operational Notes

- Back up the installation before upgrading.
- Keep server and Companion versions aligned, especially around auth and upload flow changes.
- For remote deployments, configure a trusted public origin with `WEB_APP_URL`.
- Treat backup archives as sensitive material.

### Companion Pairing and Security Resets

- The Companion app forms a strict 1-to-1 association with a single backend.
- Users must manually re-pair the Companion from its settings by choosing `Generate New Pairing Code` if they switch to a different Nojoin deployment, or if the backend's identity or URL changes.
- The Companion pins the backend TLS certificate it first sees during pairing. Replacing or rotating that backend certificate requires an explicit re-pair.
- Companion secrets are no longer stored in plaintext config. On Windows, they are moved into a DPAPI-protected secret bundle tied to the active pairing.
- Using Disconnect Current Backend in Companion Settings clears the saved backend certificate trust and local secret bundle, then attempts a best-effort remote revoke. Users can still switch backends even if the old backend is offline.
- Major security upgrades to the Companion will drop legacy trust state. After such upgrades, users will be required to perform a clean first-pair workflow before they can record.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md)
- [CALENDAR.md](CALENDAR.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [USAGE.md](USAGE.md)
