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

1. Open **Settings > Backup and restore**.
2. Choose whether to include audio recordings.
3. If including audio, choose the archive quality (see below).
4. Start the export.
5. Store the resulting ZIP file somewhere secure.

The download streams straight to disk, so a large archive does not need to fit in browser memory, and an interrupted download can be resumed.

### Archive Quality

**Compressed** (the default) re-encodes audio to Opus, producing a much smaller archive. Audio that is already Opus is copied unchanged rather than re-encoded.

**Original** stores every recording exactly as captured. The archive is substantially larger, but a restored recording can be reprocessed without compounding compression loss. Choose this if you may want to re-run transcription or diarisation on restored meetings.

Each recording's audio is selected from its database record, so the master recording is always the file that is archived, never its playback proxy.

### Recordings Without Audio

If a recording's audio file is missing from disk when the backup runs, its metadata, transcript and notes are still archived and you are told how many recordings were affected, both at download time and inside the archive's `backup_info.json`. Those recordings restore without playable audio.

## Restoring a Backup

1. Open **Settings > Backup and restore**.
2. Upload the backup ZIP.
3. Choose the conflict mode.
4. Wait for the import to finish before closing the page.

Only one restore can run at a time on a server. Starting a second while one is in progress is refused.

The restore validates the whole archive before changing anything: it checks the archive format version, confirms every record file is readable, and confirms there is enough free disk space. A backup that fails any of these checks is refused without touching your data.

### Conflict Modes

#### Skip

- Keeps the current copy when a conflicting record already exists, including its audio file.
- Safest for additive merges into an active installation.

#### Overwrite

- Replaces the current copy with the backup version when conflicts are found.
- Useful when the backup should become the source of truth.

#### Clear All Existing Data

- Empties every table except users, and clears the recordings directory.
- User accounts are preserved to prevent lockout.

## If Something Goes Wrong

A restore is applied as a single database transaction, and files are only moved into place once that transaction has committed. If a restore fails at any point, the installation is left exactly as it was, including when **Clear All Existing Data** was selected.

## Partial Restores

A restore can succeed while being unable to bring every record across. When that happens the result is reported as completed with warnings, and the page shows which kinds of record were skipped and why, rather than reporting a clean success. The most common reason is that a record's owner or parent could not be matched in the target installation.

## Practical Restore Notes

- Ownership mappings are preserved so restored records belong to the correct users.
- Audio and recordings are matched carefully to reduce duplicate restoration.
- Calendar connections, selections, cached events, and each meeting's link to its calendar event are restored so the dashboard calendar comes back intact.
- Redacted AI credentials (such as LLM API keys and Hugging Face tokens) must be configured in the target server's environment variables (e.g., `.env`) and the containers restarted.
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

Manual transcript and speaker corrections **do** survive, because they are recorded in the transcript itself. What is not preserved is audit history: the record of who changed what and when, per-window diarisation results, and confidence scores. If you need backups as audit evidence, this archive is not that. See [ADR-0003](adr/0003-rebuild-canonical-pipeline-state-on-restore.md) for why.

## Archive Compatibility

Archives record a format version. A Nojoin installation restores any archive at or below the format version it understands, so older backups keep working after an upgrade. An archive from a **newer** Nojoin than the one restoring it is refused with a clear message; upgrade the server first.

## Cross-System Restores

Restoring a backup onto a different installation preserves the original `public_id` of each recording so recording URLs, document relationships and later backups remain stable. Browser live recording state is not portable across systems; users should finish, resume, or discard paused recordings before backup and restore operations whenever possible.

## Housekeeping

Exported archives, uploaded archives and abandoned uploads are reclaimed automatically by a periodic cleanup task. Exports remain downloadable for a period after they are created rather than being deleted on first download, so an interrupted download can be retried.

Two environment variables are relevant on large installations:

- `UPLOAD_LIMIT_BACKUP`: the largest archive that may be uploaded for restore. Defaults to 25 GB. Raise it if you take Original-quality backups of a very large library.
- `BACKUP_EXPORT_DIR`: where exported archives are written. Defaults to `/tmp/nojoin_backups`, which is a volume shared between the API and the workers.

## Recommendations

- Create backups before upgrades.
- Keep at least one offline copy.
- Restrict access to backup archives.
- Test restore procedures before you rely on them operationally.

## Related Docs

- [ADMIN.md](ADMIN.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [CALENDAR.md](CALENDAR.md)
- [ADR-0003: Rebuild canonical pipeline state on restore](adr/0003-rebuild-canonical-pipeline-state-on-restore.md)
