# Nojoin Administration Guide

This guide is for Owners and Admins of a self-hosted Nojoin installation. [USAGE.md](USAGE.md) covers everyday use, and [DEPLOYMENT.md](DEPLOYMENT.md) covers the stack and `.env`.

## Roles

| Role | Access |
| --- | --- |
| Owner | Everything an Admin has, plus creating, promoting, changing and deleting Owners. |
| Admin | Users, invitations, and the administrator settings. |
| User | Their own recordings, tags, People library, settings and connected services. |

Some actions also need superuser rights, which only the account created at first-run setup (the setup account) has: editing an existing user's username, password, active status or AI analysis limit, backup and restore, and viewing or downloading logs. Settings shows these to the setup account alone. Other Owners and Admins can still create and delete accounts and change roles in the users table, within the Owner limits above.

An installation always keeps at least one active Owner, since only an Owner can make another. Nojoin refuses to change the role of, deactivate or delete the last one and says why, so make another account an Owner first. The same goes for superuser rights: Nojoin refuses to deactivate or delete the last active account that has them, or to take them away from it. No account can deactivate itself, so the setup account cannot sign itself out for good. Each rule holds when two accounts change each other at the same moment: one change goes through and the other is refused.

## Users and Invitations

Registration is by invitation only. **Settings > Users and access** has three parts:

- **Invitations** creates a sign-up link for the User or Admin role, valid for 1 to 365 days (default 7) and 1 to 100 uses (default 1). Revoking a link stops it working and keeps it listed; a revoked link can then be deleted. People who join through a link choose their own password.
- **Users** creates an account directly with a temporary password. The new user must change it at first sign-in, and until then can only view their profile, change the password and sign out. Resetting someone's password applies the same rule, signs them out everywhere and disconnects their connected apps, such as Claude and Codex. Deactivating an account disconnects them too.
- **AI analysis** caps the meeting analyses each person can start per day, counted from midnight UTC. Each analysis is one long call against that person's AI provider quota. Leave it blank or 0 for no cap. To give one person a different cap, the setup account edits them in the users table: blank uses the installation's cap, 0 means none.

Passwords need at least 8 characters, and one made entirely of whitespace is refused. Passwords set before this rule keep working until they are next changed or reset.

## Settings Structure

Settings is grouped by subject. The setup account sees fourteen categories, other Owners and Admins thirteen, and everyone else nine. A link to a category the account cannot see opens the settings index instead.

| Group | Category | Who sees it |
| --- | --- | --- |
| General | Profile and security | Everyone |
| | Users and access | Administrators |
| | Appearance | Everyone |
| | Integrations | Everyone (calendar sign-in credentials are administrator-only) |
| Meetings | Recording | Everyone |
| | Transcription | Everyone (engine and model are administrator-only) |
| | Notes and live assistance | Everyone |
| | Your AI | Everyone |
| | AI providers | Administrators |
| Data | Backup and restore | The setup account |
| | Privacy | Administrators |
| | System and logs | Administrators (the logs: the setup account) |
| About | Updates | Everyone |
| | Help | Everyone |

Each category has its own route, such as `/settings/recording`, and older `?tab=` links redirect to the matching category.

Settings that are rarely changed, that can degrade output if set wrong, that need outside credentials, or that only matter with several users sit in a collapsed **Advanced** block at the foot of their category. Its header shows how many settings inside differ from their default and how many need attention, and it opens when a search result or link points inside it. Search finds individual settings, including those behind **Advanced**.

On pages everyone can open, a setting marked **Everyone** applies to the whole installation and one marked **This browser** is kept only in that browser. Everything on the administrator-only pages applies to the whole installation, so those pages carry no marks.

## Administrator Settings

LLM provider API keys and the Hugging Face token are set only in `.env`, followed by a restart. No settings field accepts them.

### AI Providers

**Settings > AI providers** sets the AI everyone uses unless they connect their own subscription ([USAGE.md](USAGE.md#your-own-subscription-cli-oauth)).

- **Default provider** shows the provider set by `LLM_PROVIDER` and lets you choose its model. No provider has a default model: until you choose one the page reads **No model selected** and every AI feature fails. **Test connection** checks the key or address and reloads the model list without saving.
- Anthropic's API does not accept a temperature setting on any Claude model, so Claude's notes and titles vary more from run to run than other providers', which Nojoin runs at a low temperature.
- With Ollama, the page shows the address from `OLLAMA_API_URL` and sets the **Context window** sent as `num_ctx`, with the GPU memory it needs at about 190 KiB per token on a 14B model. `OLLAMA_CONTEXT_WINDOW` in [DEPLOYMENT.md](DEPLOYMENT.md) explains sizing, and [USAGE.md](USAGE.md#long-meetings-and-ollama) what happens to a meeting longer than the window.
- **Meeting Edge** turns live guidance on or off for everyone and chooses its model.
- Under **Advanced**:
  - **Fallback provider** comes from the `SECONDARY_` variables ([DEPLOYMENT.md](DEPLOYMENT.md#configure-env)); choose its model here. Without a model the fallback is off, the workers log a warning once, and the **Advanced** block reads **1 needs attention**. With Ollama as the primary, a hosted fallback is recommended for long meetings.
  - **Hugging Face** shows whether `HF_TOKEN` is set. Speaker separation runs from the bundled models without it; the token is needed only to download them again from Hugging Face.
  - **Model downloads** lists the speech and speaker models with their state: **Ready**, **Bundled**, **External** or **Missing**. **Download** fetches a missing model with progress in place, one at a time. **Delete** removes a downloaded one, or clears a download that stopped part-way. Deletion runs on a worker, so one must be running. Bundled models cannot be deleted, and neither can a Pyannote model found in the personal cache (`~/.cache/huggingface/hub`), which is still used and shows as **External**.
  - **Subscription usage** shows the tokens each person sent through their own Claude or ChatGPT subscription over the last 7 days, the last 30 days and all time, with a request count and a **Status** of **OK**, **Approaching** or **Limited until** a reset time. Usage is shown in tokens, never money.

### Transcription

The transcription engine and model, under **Advanced** in **Settings > Transcription**, apply to every user and are stored in `data/config.json`. Choosing a model that is not on the server asks whether to download it now or on first use. Waiting is safe, but live transcription and Meeting Edge wait for the download when it is first needed. Parakeet and Canary transcribe in windows of at most 30 seconds ([DEPLOYMENT.md](DEPLOYMENT.md#gpu-acceleration)).

### Notes Structure and Glossary

**Notes structure** (in **Settings > Notes and live assistance**) and **Glossary** (in **Settings > Transcription**) each have a shared value you maintain for everyone, beside each user's own.

- Shared notes structures are visible to everyone and editable only by an administrator. Mark one as the shared default for anyone who has not chosen their own. Users who want different text copy a shared structure into their own list.
- Only the section structure is editable. Accuracy, attribution, table formatting and the response format are fixed by Nojoin.
- Each set of notes keeps the structure text it was generated from, so editing or deleting a structure never changes past notes.
- The shared glossary is read-only to users and merged with each user's own. Where both define a term, the user's definition wins.

### Calendar

**Settings > Integrations**, under **Advanced**, holds the installation's Google and Microsoft calendar sign-in credentials. [CALENDAR.md](CALENDAR.md) covers registering the apps.

### Exported Files

**Settings > Appearance > Exported files** sets whether PDF, Word and text exports carry Nojoin branding: the logo and name on the letterhead, and a "Generated by Nojoin" footer line linking to nojoin.co.uk. It is on by default and applies to everyone. Off, exports keep the same layout on a plain letterhead in neutral slate, for documents sent under your own name. The analytics explanations still name Nojoin where they say how a figure was measured.

### Privacy

**Settings > Privacy** turns the six-hourly usage diagnostics on or off and shows this installation's random ID, the endpoints, what each payload contains, and when each was last sent or why it failed. The daily install heartbeat is shown too but has no switch here; `NOJOIN_TELEMETRY_ENABLED=false` in `.env` stops it. [TELEMETRY.md](TELEMETRY.md) has the full disclosure.

### Backup and Restore

**Settings > Backup and restore** exports and restores the whole installation, and only the setup account sees it. Read [BACKUP_RESTORE.md](BACKUP_RESTORE.md) first: an archive holds every user's password hash and calendar credentials, so treat it as a secret.

### System and Logs

**Settings > System and logs** shows:

- Service health, refreshed every 30 seconds: database, queue, worker, FFmpeg, storage, transcription model, speaker separation, processing device and AI. The AI check reads **Configured** when the default provider has a credential and a model, **No model selected** when it has a credential but no model, **Fallback only** when only the fallback can answer, and **Not configured** when there is no provider.
- Live logs from one service or all of them, filtered by text, regular expression or level. Only the setup account sees these. **Download** saves the chosen service's whole log, or with **All services** the newest 10,000 lines of each service in one file, in time order and marked with the service's name as in the live view.

### Updates

**Settings > Updates** shows the installed and latest versions and each release's notes ([USAGE.md](USAGE.md#updates)). Administrators also see the upgrade commands, and Owners and Admins get a notification when a new release is out. Users without an administrator role see neither.

## Operational Notes

- Back up before upgrading ([DEPLOYMENT.md](DEPLOYMENT.md#upgrading-and-migration)).
- For remote access, set a trusted public origin with `WEB_APP_URL` ([DEPLOYMENT.md](DEPLOYMENT.md#remote-access-and-trusted-public-origin)).
- [CAPTURE.md](CAPTURE.md) covers browser support and missing audio. For a failed upload, transcode, live transcription, finalise or discard, check the API and worker logs.
- Paused recordings, and captures interrupted without a pause, are kept indefinitely and never cleaned up automatically.
