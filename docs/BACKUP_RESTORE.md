# Nojoin Backup and Restore Guide

**Settings > Backup and restore** backs up the whole installation to a ZIP archive and restores one. Both need superuser rights, which only the account created at first-run setup has ([ADMIN.md](ADMIN.md#roles)).

## What a Backup Contains

- Recordings with their transcripts, notes, speakers, tags, chat history and attached documents (records and files).
- Tasks, with their tags and recording links.
- People records and voiceprints.
- User accounts with their password hashes and settings, and the shared notes structures.
- Calendar sign-in credentials, each connected calendar's tokens, selected calendars, colours and cached events, and each meeting's link to its event.
- The system configuration, with keys, tokens and passwords redacted.
- Optionally, each recording's audio.

## What Is Not Included

- **Set again on the target:** LLM provider API keys and the Hugging Face token, which live in `.env`, and each person's own Claude or ChatGPT subscription, which they reconnect in **Settings > Your AI**.
- **Tied to the source installation:** MCP connector registrations and tokens (reconnect each assistant), calendar push channels, outstanding invitations, and the session revocation list. Subscription usage figures and the day's AI analysis counts start again from zero.
- **Live capture data:** audio that paused or in-progress browser recordings have not yet finalised. Finish, resume or discard paused recordings before backing up.
- **Rebuilt after a restore:** playback files, the meeting chat search index, and the transcript pipeline's per-line records. Restored recordings may show as processing while this runs. Manual transcript and speaker corrections survive, along with the locks that stop a reprocess overwriting them. Their edit history (who changed what and when), per-window speaker separation results and confidence scores do not, so an archive is not audit evidence.

## Sensitive Contents

> **Warning:** the archive is an unencrypted ZIP holding every user's password hash, the calendar sign-in client secrets (including any set in `.env`), and each connected calendar's access and refresh tokens. The calendar secrets are stored decrypted so that they restore onto an installation with a different encryption key. Store and move archives as you would a secrets file.

## Creating a Backup

1. Open **Settings > Backup and restore** and select **Back up now**.
2. Turn **Include audio** on or off. Without audio the archive is much smaller, but restored meetings cannot be played.
3. With audio, choose **Compressed** or **Original** (see [Archive Quality](#archive-quality)).
4. Select **Create backup**, and store the downloaded ZIP somewhere secure.

If nothing has changed since the saved backup of the same kind was made, that one downloads at once. Otherwise Nojoin builds a new one in the background, which can take hours for a large library with audio. You can keep working or close the tab. When it is ready, a notification with **Download Now** appears. It stays until you use or close it, and comes back if you reload or return later in the same browser. The download streams to disk, and an interrupted one can be resumed.

### The Saved Backup

Nojoin keeps the most recent finished backup on the server. **Settings > Backup and restore** shows when it was made, what kind it is, its size, and **Up to date** or **Changes since**, with **Download** and **Delete** buttons. Its time is the moment its data was read, and the downloaded file is named for that time.

It stays until a newer backup replaces it or you delete it, and downloading it does not remove it. A new backup is built beside the old one, which is removed only once the new one is complete, so allow disk space for both, especially with Original quality.

Any change to what it carries makes it out of date, including audio or document files changing on disk and upgrading Nojoin. The calendar sync's routine writes every fifteen minutes (refreshed tokens, sync cursors and cached events) do not count, and restoring the older copy loses nothing because a restored connection syncs again. Connecting or disconnecting a calendar, or changing which calendars are selected or their colours, does count.

### One Backup at a Time

One backup is built at a time. Asking for the same kind while it runs follows the running one, and asking for a different kind is refused until it finishes. A backup is refused while a restore runs, and a restore while a backup is being built.

The browser that asked is notified when it is ready. Opening **Settings > Backup and restore** on another device shows the progress there and notifies that browser too.

A backup that reports no progress for 30 minutes is abandoned, because its worker restarted or never picked it up, and the next request starts a new one. A large backup reports progress after every recording, so one is never abandoned for taking a long time.

### Archive Quality

**Compressed** (the default) re-encodes every recording's audio to 64 kbps Opus, whatever its format; only `.opus` files are copied unchanged. Each recording keeps one audio track and no video. In testing, re-encoding ran at about 260 times real time, roughly half a minute per two-hour recording, so a large library can take hours.

**Original** stores each recording's file exactly as captured, with every track. The archive is much larger, but restored recordings can be reprocessed without compounding compression loss, so choose it if you may re-run transcription or speaker separation. Imports keep only their audio, but a video imported by an earlier version still carries its video, which can be several gigabytes for a long screen recording.

Either way, the archive holds each recording's master audio, never its playback copy.

A large library can produce an archive bigger than the restore upload limit, which is 25 GB by default and checked only when the archive is uploaded for restore. 2,000 two-hour browser recordings come to about 290 GB in Original quality and 115 GB in Compressed. Raise [`UPLOAD_LIMIT_BACKUP`](#housekeeping) on the server that will restore it before you need it.

### Recordings Without Audio

If a recording's audio file is missing, empty or unreadable, cannot be re-encoded, or clashes with another recording's name in the archive, its metadata, transcript and notes are still archived. The download tells you how many recordings were affected, and `backup_info.json` in the archive records the counts (`recordings_without_audio`, `recordings_audio_failed`, and `documents_without_files` and `documents_failed` for attached documents). Those recordings restore without playable audio.

Archives made before 2.6.0 left out the audio of WebM browser recordings and of AAC, MP4, WMA and WebM imports, so those recordings are metadata only. Take a new backup to capture their audio.

> **Warning:** restoring one of those older archives with **Clear all existing data** deletes those recordings' audio from the server, because the archive has none to put back. **Skip** keeps the audio already on disk. **Overwrite** keeps it when the file has the name it had when the backup was taken, as on the server the backup came from, and otherwise deletes it with the meeting it replaces. If you need to clear existing data, take a new backup and restore that instead.

## Restoring a Backup

1. In **Settings > Backup and restore**, choose the backup ZIP under **Restore** and select **Restore**.
2. Choose what happens to meetings that are already here (**Skip** or **Overwrite**), or turn on **Clear all existing data**, then select **Start restore**.
3. Keep the tab open until the upload finishes. The server then restores the archive and you can leave: a notification reports the result, and the page lists any records that could not be restored until you dismiss them. Reload Nojoin afterwards to see the restored data.

Before changing anything, the restore checks the archive's format version, that every record file is readable, and that there is enough free disk space, and refuses an archive that fails any of them. The database changes are a single transaction, and files move into place only after it commits, so a restore that fails at any point leaves the installation as it was, even with **Clear all existing data**.

### Conflict Modes

| Mode | Effect |
| --- | --- |
| **Skip** (default) | A meeting already here keeps its current copy, audio included. Only new meetings are added. |
| **Overwrite** | A meeting already here is deleted, with everything attached to it, and replaced by the backup's copy. Once the restore has committed, its audio, playback file and document files are deleted from disk, except a file the backup's copy uses: a meeting restored without audio keeps the audio file of the same name already here. |
| **Clear all existing data** | Deletes every recording, transcript, document, task, tag, person, calendar connection and other archived record, and empties the recordings and documents folders, before restoring. User accounts are kept so nobody is locked out. Only this mode restores the archived system configuration. |

A meeting counts as already here when its `meeting_uid` or `public_id` matches, or, for archives older than those identifiers, its audio file name. Both identifiers are restored, so recording links keep working and later backups from the same source merge without duplicates. A restored recording whose file name or identifier clashes with an unrelated one here gets a new one instead of failing.

### What Else to Expect

- A user whose username already exists here keeps their current account and password, and the archive's records for that username are given to them. Other users are created with their archived password hashes.
- Recordings that were still processing when the backup was taken are restored as processed if their transcript came across, and as errored otherwise. Reprocess an errored one to finish it.
- A restore can finish while skipping some records, most often because their owner or parent could not be matched. The notification then says how many could not be restored, and the page lists which kinds of record were skipped and why under **Some records could not be restored**.

### Archive Compatibility

Archives carry a format version. A server restores any archive at or below the format version it understands, so older backups keep working after an upgrade. An archive with a newer format version is refused with a message to upgrade the server first. An archive from a newer Nojoin release with the same format version still restores, with a warning in the API log.

An archive older than format 3 holds each meeting's notes as one text, which restores as a single note at the top of the meeting. The note is added only when the restore inserts the transcript, so Skip mode or a second restore adds no duplicate.

## Housekeeping

The saved backup lives in `data/backups`, and uploads and restore staging under `data/`; [DEPLOYMENT.md](DEPLOYMENT.md) describes the layout. Uploaded archives, abandoned uploads, restore staging and archives from failed backups are deleted once they are 24 hours old. The saved backup is not.

- `UPLOAD_LIMIT_BACKUP`: the largest archive that may be uploaded for restore, in bytes. Defaults to 25 GB. `docker-compose.example.yml` does not pass it through from `.env`, so add it under the `api` service's `environment:` in your `docker-compose.yml`, for example `UPLOAD_LIMIT_BACKUP: "107374182400"` for 100 GB, then recreate the container with `docker compose up -d api`. Give it a number: an empty value stops the API from starting.
- `BACKUP_EXPORT_DIR`: where archives are written, `data/backups` by default. Set it to put them on another disk. The API and the workers must agree on the value, or the API cannot serve what a worker wrote.

Back up before every upgrade, keep at least one copy offline, and test a restore before you rely on it.
