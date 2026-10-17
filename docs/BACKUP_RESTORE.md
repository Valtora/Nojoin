# Nojoin Backup and Restore Guide

Nojoin includes a full-system backup and restore flow under **Settings > Backup and restore**.

This guide explains what is included, what is deliberately left out, and what should be treated as sensitive during handling.

## What a Backup Contains

A backup archive contains:

- Database records for meetings, transcripts, notes, speakers, tags and chat history.
- Dashboard and Tasks workspace state such as Task List items, task archive state, task tag links, and task recording links.
- User accounts, including their password hashes, so restored users can sign in.
- People records and stored voiceprint embeddings.
- Calendar provider configuration.
- Connected-calendar tokens, selected calendars, sync cursors, colour overrides, and cached events.
- Audio recordings, optionally included, either compressed or byte-for-byte.
- Attached documents, both their records and the files themselves.
- System configuration with sensitive application keys redacted.

## What Is Not Included

The following are deliberately absent from the archive. Each is either a secret that belongs to the target installation, or data Nojoin can rebuild.

**Credentials that must be reconfigured on the target:**

- LLM provider application keys.
- Hugging Face style application keys and tokens.
- CLI OAuth subscription credentials. Reconnect these in **Settings > Your AI**.

These must be set in the target installation's environment variables (e.g. `.env`) and the containers restarted if they are not already set.

**Installation-local state, which would be wrong on another host:**

- OAuth server registrations, authorisation codes and refresh tokens.
- The JWT revocation list.
- Calendar push notification channels, which are bound to the source installation's public URL.
- Outstanding user invitations.

**Data that is rebuilt after a restore rather than carried:**

- Playback proxy files.
- The RAG index used by meeting chat.
- The canonical transcript pipeline's utterance graph and its audit history.

## Sensitive Backup Contents

Backups intentionally preserve enough information to restore user accounts and the dashboard calendar experience on another installation.

That means the archive can contain:

- Every user's password hash.
- Calendar provider client credentials.
- Connected-account access and refresh tokens.

Treat the archive like a secrets file, not just a convenience export.

## Creating a Backup

1. Open **Settings > Backup and restore** and select **Download Backup**.
2. Choose whether to include audio recordings.
3. If including audio, choose the archive quality (see below).
4. Select **Create Backup**.
5. Store the downloaded ZIP file somewhere secure.

If nothing has changed since the saved backup of the same kind was made, that backup downloads straight away. Otherwise Nojoin prepares a new one in the background. For a large library with audio that can take hours, and you can keep working, leave the page or close the tab meanwhile. When the backup is ready, a notification appears with a **Download Now** button. It stays until you use it or close it, and it comes back if you reload the page or return later in the same browser.

The download streams straight to disk, so a large archive does not need to fit in browser memory, and an interrupted download can be resumed.

### The Saved Backup

Nojoin keeps the most recent finished backup on the server. **Settings > Backup and restore** shows when it was made, what kind it is, its size and whether it is still up to date, with buttons to download it again or delete it. Its date is the moment its data was read, and the downloaded file is named for that time rather than for the download.

It stays until a newer backup replaces it or an administrator deletes it, so it occupies as much disk as the archive does. A new backup is built beside the old one, and the old one is removed only when the new one is complete, so allow room for both, especially with Original quality on a large library.

The saved backup is out of date once anything it would carry has changed: a recording added, deleted or edited, a transcript or its notes, speakers, people and voiceprints, tags, tasks, attached documents, chat history, users and their settings, the system configuration, or an audio or document file on disk. Upgrading Nojoin makes it out of date as well, so the first backup after an upgrade is always a new one.

What the calendar sync writes does not count. Every fifteen minutes it refreshes each connected calendar's access token, sync timestamps and cursor, and its copy of the events, so counting those would make every backup out of date within a quarter of an hour. Nothing is lost by restoring the older copy: a restored calendar connection syncs again and fetches whatever changed since. Connecting or disconnecting a calendar, or changing which calendars are selected or their colours, does count.

### One Backup at a Time

Only one backup is prepared at a time. Asking for the same kind of backup while one is being prepared follows the one already running, and asking for a different kind is refused until it finishes. Backups and restores never overlap: each is refused while the other is running, because a restore rewrites the data a backup is reading.

The notification goes to the browser that asked for the backup. Opening **Settings > Backup and restore** on another device while a backup is being prepared shows its progress there, and that browser is notified too.

A backup that reports no progress for 30 minutes is abandoned. That happens when its worker was restarted or never picked it up, and the next request starts a new one. A large backup that is still working reports progress after every recording, so it is never abandoned for taking a long time.

### Archive Quality

**Compressed** (the default) re-encodes audio to 64 kbps Opus, producing a much smaller archive. Every recording is included whatever its format: browser recordings (FLAC, or WebM for those made before FLAC capture), and imports in the format they were stored in. Only a `.opus` file is copied unchanged. Everything else is re-encoded, including browser recordings and Ogg files. In testing, re-encoding ran at roughly 260 times real time, about half a minute per two-hour recording, so backing up a large library of browser recordings can take hours. The archive keeps one audio track per recording, with no video: the track marked as default, or if none is, the one with the most channels.

**Original** stores every recording exactly as captured, in its own file format, with every track it has. Imports keep only their audio, so a screen recording is archived at the size of its audio track. An MP4 or WebM imported with its video by an earlier version still carries that video, which can be several gigabytes for a long screen recording. The archive is substantially larger, but a restored recording can be reprocessed without compounding compression loss. Choose this if you may want to re-run transcription or diarisation on restored meetings.

An audio file whose extension is a compressed audio format is stored in the ZIP as it is, because compressing it again gains nothing. WAV files, which hold uncompressed audio, are compressed in an Original-quality archive. The choice goes by the file extension, not the contents, so another container that carries uncompressed audio is stored at full size. An imported video file is not one of them: import stores its uncompressed audio as FLAC.

A large library can produce an archive bigger than the restore upload limit, which is 25 GB by default and is only checked when the archive is uploaded for restore. A library of 2,000 two-hour browser recordings is about 290 GB in Original quality and about 115 GB in Compressed quality. Raise `UPLOAD_LIMIT_BACKUP` on the server that will restore it before you need it (see [Housekeeping](#housekeeping)).

Each recording's audio is selected from its database record, so the master recording is always the file that is archived, never its playback proxy.

### Recordings Without Audio

If a recording's audio file is missing from disk or empty when the backup runs, or cannot be read, re-encoded or given a unique name in the archive, its metadata, transcript and notes are still archived and you are told how many recordings were affected, both at download time and inside the archive's `backup_info.json` (`recordings_without_audio`, `recordings_audio_failed`). Those recordings restore without playable audio, and their player says the audio is not available.

Backups from earlier versions archived audio only for WAV, MP3, M4A, OGG, FLAC and Opus recordings. Browser recordings made since v1.1.0 (stored as WebM) and AAC, MP4, WMA or WebM imports were left out of the archive. Backups taken with v1.7.0 or later counted them among recordings that had no audio file on disk, and earlier versions did not report them. Those recordings are metadata only in such an archive. Take a new backup to capture their audio.

> **Warning:** restoring one of those earlier archives with **Clear All Existing Data** deletes the audio of those recordings from the server, because the archive has none to put back. **Skip** and **Overwrite** keep the audio already on disk. If you need Clear All, take a new backup and restore that instead.

## Restoring a Backup

1. Open **Settings > Backup and restore**.
2. Upload the backup ZIP.
3. Choose the conflict mode.
4. Keep the tab open until the upload finishes. The server then restores the archive, and you can leave the page: a notification reports the result, and the settings page shows any records that could not be restored until you dismiss them. Reload Nojoin afterwards to see the restored data.

Only one restore can run at a time on a server. Starting a second while one is in progress is refused, and so is starting one while a backup is being prepared.

The restore validates the whole archive before changing anything: it checks the archive format version, confirms every record file is readable, and confirms there is enough free disk space. A backup that fails any of these checks is refused without touching your data.

### Conflict Modes

#### Skip

- Keeps the current copy when a conflicting record already exists, including its audio file.
- Safest for additive merges into an active installation.

#### Overwrite

- Replaces the current copy with the backup version when conflicts are found.
- Useful when the backup should become the source of truth.

#### Clear All Existing Data

- Empties every table except users, and clears the recordings and documents directories.
- User accounts are preserved to prevent lockout.

## If Something Goes Wrong

A restore is applied as a single database transaction, and files are only moved into place once that transaction has committed. If a restore fails at any point, the installation is left exactly as it was, including when **Clear All Existing Data** was selected.

## Partial Restores

A restore can succeed while being unable to bring every record across. When that happens the result is reported as completed with warnings, and the page shows which kinds of record were skipped and why, rather than reporting a clean success. The most common reason is that a record's owner or parent could not be matched in the target installation.

## Practical Restore Notes

- Ownership mappings are preserved so restored records belong to the correct users.
- Audio and recordings are matched carefully to reduce duplicate restoration.
- Calendar connections, selections, cached events, and each meeting's link to its calendar event are restored so the dashboard calendar comes back intact.

- Recordings that were still processing when the backup was taken are restored as processed if their transcript came across, and as errored otherwise, so nothing is left permanently stuck. Reprocess an errored recording to finish it.

## Recording Identity and Matching

Each recording carries two stable, server-generated identifiers in addition to its internal numeric id:

- `meeting_uid`: durable cross-system identifier for the meeting.
- `public_id`: identifier exposed in URLs, recording links, document relationships, and browser recording APIs.

Both are preserved in the backup archive and re-applied on restore so that:

- Document links, recording URLs, and external references that target a recording's `public_id` keep working after a restore.
- Subsequent backups taken from the same source remain mergeable into the same target without producing duplicate recording rows.

When restoring, conflicting recordings are detected by matching **any** of `meeting_uid`, `public_id`, or (for legacy backups created before these columns existed) the audio file's stem. The Skip and Overwrite conflict modes apply to the whole matched recording, so you do not need to deduplicate manually.

If a target installation already holds a row with the same `public_id` or `audio_path` as an inbound recording but no matching `meeting_uid` (an unusual edge case caused, for example, by hand-edited archives), the restore stores the inbound recording under a fresh identifier rather than aborting the import.

## Rebuilt After Restore

Playback proxies, the meeting-chat RAG index, and the canonical transcript pipeline's utterance graph are not carried in the archive. They are rebuilt automatically after a restore, so newly restored recordings may briefly show as still processing.

Manual transcript and speaker corrections **do** survive: the transcript carries the flags that mark text and speakers as manually edited, and the rebuild restores the locks that stop a later reprocess overwriting them. What is not preserved is audit history: the record of who changed what and when, per-window diarisation results, and confidence scores. If you need backups as audit evidence, this archive is not that.

## Archive Compatibility

Archives record a format version. A Nojoin installation restores any archive at or below the format version it understands, so older backups keep working after an upgrade. An archive from a **newer** Nojoin than the one restoring it is refused with a clear message; upgrade the server first.

## Cross-System Restores

Browser live recording state is not portable across systems; users should finish, resume, or discard paused recordings before backup and restore operations whenever possible.

## Housekeeping

Uploaded archives, abandoned uploads, and exports left behind by a backup that failed are reclaimed after 24 hours by a periodic cleanup task. The saved backup is not: it stays until a newer one replaces it or you delete it (see [The Saved Backup](#the-saved-backup)), and it is not deleted on download, so an interrupted download can be resumed.

An export is built by a worker and served by the API, so it is written to `data/backups`, inside the data directory every service already mounts. The saved backup is the `nojoin-backup-*.zip` file there, beside a `latest-backup.json` manifest that records what it contains.

Uploaded archives take the opposite path, through `data/temp_uploads`, and restore staging happens in `data/restore_staging`. Every backup working file therefore lives under the data directory, and none of them depend on a shared `/tmp`.

Two environment variables are relevant on large installations:

- `UPLOAD_LIMIT_BACKUP`: the largest archive that may be uploaded for restore, in bytes. Defaults to 25 GB. Raise it if you back up a large library, Original quality especially. `docker-compose.example.yml` does not pass it through from `.env`, so add it under the `api` service's `environment:` in your `docker-compose.yml`, for example `UPLOAD_LIMIT_BACKUP: "107374182400"` for 100 GB, then recreate the container with `docker compose up -d api`. Give it a number: an empty value stops the API from starting.
- `BACKUP_EXPORT_DIR`: where exported archives are written. Defaults to `data/backups`. Set it to move exports onto a separate disk. The API and the workers must agree on the value, or the API cannot serve what a worker wrote.

## Recommendations

- Create backups before upgrades.
- Keep at least one offline copy.
- Restrict access to backup archives.
- Test restore procedures before you rely on them operationally.

## Related Docs

- [ADMIN.md](ADMIN.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [CALENDAR.md](CALENDAR.md)
