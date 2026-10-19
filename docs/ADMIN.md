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

## Settings Structure

Settings is organised by domain rather than by role. Categories that only an
administrator can act on are simply not shown to anyone else, so an
administrator sees fourteen categories and everyone else sees nine.

| Group | Category | Who sees it |
| --- | --- | --- |
| General | Profile and security | Everyone |
| | Users and access | Administrators |
| | Appearance | Everyone |
| | Integrations | Everyone (provider credentials are administrator-only) |
| Meetings | Recording | Everyone |
| | Transcription | Everyone (engine and model are administrator-only) |
| | Notes and live assistance | Everyone |
| | Your AI | Everyone |
| | AI providers | Administrators |
| Data | Backup and restore | Administrators |
| | Privacy | Administrators |
| | System and logs | Administrators |
| About | Updates | Everyone |
| | Help | Everyone |

Each category is its own route, such as `/settings/recording`, so a link to a
specific area can be shared or bookmarked. Pre-existing `?tab=` links still
work and redirect to the matching category.

Settings that ship with a sensible default, that can quietly degrade output if
set wrong, that need credentials obtained elsewhere, or that only matter on a
multi-user installation are collapsed behind an **Advanced** block at the foot
of their category. The block opens itself when a search result lives inside it,
and shows a count when anything inside differs from its default, so nothing is
ever hidden without a trace. A category never hides all of its content.

The search box matches individual settings rather than categories, so searching
for a control by name reaches it directly, including one behind the Advanced
gate.

## Administration Settings Areas

### Calendar

Use **Settings > Integrations** to save installation-wide Google and Microsoft OAuth credentials.

Read [CALENDAR.md](CALENDAR.md) for the full provider registration and tenant guidance.

### AI and Models

Use **Settings > AI providers** for installation-wide provider defaults, model operations, and Ollama configuration.

> [!IMPORTANT]
> For security, LLM provider API keys and Hugging Face tokens are server-side environment-only variables and must be configured via environment variables (e.g., `.env`) and the container restarted, rather than through UI settings fields.

Admin-only sections let you:

- Choose the default LLM provider. Current Anthropic models do not accept a temperature setting, so their notes and titles vary more from run to run than other providers', which Nojoin runs at a low temperature. The limit is in Anthropic's API. Older Claude models that still accept the setting get the same value as the other providers.
- Cap the AI analysis runs each user may start per day (**Daily AI analysis budget**). Each run is one long call against the user's own provider quota. Leave it blank or 0 for no cap. The cap is the install's default; an individual user's cap can be raised or lowered when editing them under Users, where 0 means no cap for that user.
- Configure the Ollama API URL and context window. The window is sent to Ollama as `num_ctx`; `OLLAMA_CONTEXT_WINDOW` in [DEPLOYMENT.md](DEPLOYMENT.md) explains how to size it, and [Long Meetings And Ollama](USAGE.md#long-meetings-and-ollama) what happens to a meeting longer than it.
- Configure a secondary LLM provider for fallback, through `SECONDARY_` prefixed environment variables ([USAGE.md](USAGE.md#secondary-llm-provider)). With Ollama as the primary, a hosted secondary is the recommended setup for long meetings.
- Choose a model for the primary provider and for the fallback. No provider has a default model, so a provider with its key or URL set but no model reads **No model selected** with a warning. Without a primary model every AI feature fails; without a fallback model the fallback is off, and the workers log a warning once.
- Choose the transcription engine and model in **Settings > Transcription**. They apply to every user on the installation and are stored in `data/config.json`. Parakeet and Canary transcribe in windows of at most 30 seconds on every host, which [DEPLOYMENT.md](DEPLOYMENT.md#gpu-acceleration) explains; the **GPU transcription window** setting that 2.7.0 added is retired.
- View installed Whisper models.
- Remove local model cache entries. Required default models are prepared on first run, and repo-bundled model assets remain read-only in the UI. Deletion runs on a worker, since the API mounts the model cache read-only, so it needs a running worker to succeed. Status and Delete look in the caches the models download into: `$XDG_CACHE_HOME/whisper` for Whisper, and the Hugging Face hub cache (`$HF_HOME/hub`, or `HF_HUB_CACHE` if set) for Parakeet, Canary and Pyannote. A Parakeet, Canary or Pyannote download that stopped part-way shows as **Missing** with a Delete button to clear it. A Pyannote model already in the personal cache, `~/.cache/huggingface/hub`, is still used, but shows as **External** and is not deleted from here.
- Choose when a newly selected transcription model is downloaded. Picking a model that is not on the server prompts you to fetch it now, so it is ready before the next recording, or to leave it until first use. Declining is safe: the model is downloaded when it is first needed, but live transcription and Meeting Edge wait for that download to finish.
- Download any model listed as **Missing** in **Model dependencies**, with progress shown in place. One preparation runs at a time, so the buttons are disabled while another is in flight.

**Notes structure** is administered from **Settings > Notes and live assistance**, and **Glossary** from **Settings > Transcription**. Both are two-tier: an install value you maintain for everyone, plus a per-user value.

- Install structures are visible to every user and editable only by an administrator. One can be marked the install default, which applies to anyone who has not chosen their own. Users can copy an install structure into their own list to vary it, which is the intended route when someone wants a change to shared text.
- The install glossary is merged into each user's own glossary rather than replacing it, so a user adding one personal term keeps the organisation's vocabulary. Where both define the same term, the user's definition is used.
- Only the section structure is editable. Accuracy, attribution, table formatting, and the response contract are fixed by the application, because other parts of Nojoin depend on them and improvements to the shipped wording have to keep reaching everyone.
- A template's structure text is snapshotted onto each set of notes it generates, so editing or deleting a template never changes what past notes were produced from. Restores remap template references; a reference that cannot be remapped is dropped rather than pointed at an unrelated template.

Each user configures **Language** in **Settings > Transcription**; [USAGE.md](USAGE.md#language-preferences) describes the options.

### Anonymous Usage Data

Use **Settings > Privacy** to turn the six-hourly usage diagnostics on or off, and to see this install's random ID, both endpoints, what each contains, and when each was last sent or why the last attempt failed. The daily install heartbeat is shown there too, with no switch: only `NOJOIN_TELEMETRY_ENABLED=false` stops it.

Read [TELEMETRY.md](TELEMETRY.md) for the full disclosure, including how upgraded installations and earlier opt-outs are handled.

### Backup and Restore

Use **Settings > Backup and restore** for export and restore operations.

Read [BACKUP_RESTORE.md](BACKUP_RESTORE.md) before relying on it operationally, especially because backup archives can contain restorable calendar credentials and connected-account tokens.

### Exported Files

**Settings > Appearance > Exported files** decides whether exported PDF, Word and text files carry the Nojoin branding: the logo and name on the letterhead, and a "Generated by Nojoin" line linking to nojoin.co.uk in every footer. It is on by default and applies to everyone on the installation. With it off, exports keep the same layout on a plain letterhead in neutral slate rather than Nojoin orange, for documents sent out under your own name. The analytics explanations still name Nojoin where they say how a figure was measured.

### System

Use **Settings > System and logs** for operational controls such as:

- Checking service health. The Optional AI check reads **Configured** only when the default provider has both its credential and a model, **No model selected** when it has a credential and no model, and **Fallback only** when only the fallback provider can answer.
- Restarting the stack.
- Viewing live logs.
- Filtering merged or per-service log output.
- Downloading log output for investigation.

### Updates

Use **Settings > Updates** to see:

- The installed server version from the current API build.
- The latest stable published release.
- Each release's notes. The installed and latest releases are open; older ones expand when chosen, and each release's container image digests sit in their own collapsed section.
- The commands that upgrade the installation. Only administrators see them.

Owners and administrators are also told about a new release by a notification with **Go to Releases**, **Skip Update** and **Remind Me**, which [USAGE.md](USAGE.md#updates) describes. Users without an administrator role do not see it, since they cannot upgrade the installation.

## Operational Notes

- Back up the installation before upgrading.
- Review release notes for browser capture, auth, and upload lifecycle changes.
- After live-pipeline upgrades, use the recording page's waveform, Meeting Edge status, and overall recording progress before treating a meeting as stuck.
- For remote deployments, configure a trusted public origin with `WEB_APP_URL`.
- Treat backup archives as sensitive material.

### Browser Capture Support

[CAPTURE.md](CAPTURE.md) has the support matrix, browser picker behaviour, and the troubleshooting steps for missing audio. Tab sharing with audio enabled is the recommended path for browser-based meetings. For a failed upload, transcode, live transcription, finalize, or discard, review the backend and worker logs.

Paused recordings are retained indefinitely and are not cleaned up automatically, which protects uploaded meeting data and prevents overlapping segment streams for the same user.

## Related Docs

- [DEPLOYMENT.md](DEPLOYMENT.md)
- [CALENDAR.md](CALENDAR.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [CAPTURE.md](CAPTURE.md)
- [TELEMETRY.md](TELEMETRY.md)
- [USAGE.md](USAGE.md)
