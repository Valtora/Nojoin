# Nojoin Backup and Restore Guide

Nojoin includes a full-system backup and restore flow under **Settings > Backup & Restore**.

This guide explains what is included, what remains redacted, and what should be treated as sensitive during handling.

## What a Backup Contains

A backup archive can include:

- Database records.
- Dashboard state such as Task List items.
- People records and stored voiceprint embeddings.
- Calendar provider configuration.
- Connected-calendar tokens, selected calendars, sync cursors, colour overrides, and cached events.
- Audio recordings, optionally included and compressed to Opus.
- System configuration with sensitive application keys redacted.

## What Is Redacted or Not Restorable

The following are intentionally not restored from backup:

- LLM provider application keys.
- Hugging Face style application keys and tokens.
- Password material.

These must be re-entered after restore when needed.

## Sensitive Backup Contents

Backups intentionally preserve enough information to restore the dashboard calendar experience on another installation.

That means the archive can contain:

- Calendar provider client credentials.
- Connected-account access and refresh tokens.

Treat the archive like a secrets file, not just a convenience export.

## Creating a Backup

1. Open **Settings > Backup & Restore**.
2. Choose whether to include audio recordings.
3. Start the export.
4. Store the resulting ZIP file somewhere secure.

If audio is included, Nojoin compresses it to Opus to reduce archive size.

## Restoring a Backup

1. Open **Settings > Backup & Restore**.
2. Upload the backup ZIP.
3. Choose the conflict mode.
4. Wait for the import to finish before closing the page.

## Conflict Modes

### Skip

- Keeps the current copy when a conflicting record already exists.
- Safest for additive merges into an active installation.

### Overwrite

- Replaces the current copy with the backup version when conflicts are found.
- Useful when the backup should become the source of truth.

## Practical Restore Notes

- Ownership mappings are preserved so restored records belong to the correct users.
- Audio and recordings are matched carefully to reduce duplicate restoration.
- Calendar connections, selections, and cached events can be restored so the dashboard calendar comes back intact.
- Redacted AI keys must still be re-entered afterwards.

## Recording Identity and Matching

Each recording carries two stable, server-generated identifiers in addition to its internal numeric id:

- `meeting_uid`: durable cross-system identifier for the meeting.
- `public_id`: identifier exposed in URLs and used by the companion upload-token flow.

Both are preserved in the backup archive and re-applied on restore so that:

- Companion pairings, document links, and external references that target a recording's `public_id` keep working after a restore.
- Subsequent backups taken from the same source remain mergeable into the same target without producing duplicate recording rows.

When restoring, conflicting recordings are detected by matching **any** of `meeting_uid`, `public_id`, or (for legacy backups created before these columns existed) the audio file's stem. The Skip and Overwrite conflict modes apply to the whole matched recording, so you do not need to deduplicate manually.

If a target installation already holds a row with the same `public_id` or `audio_path` as an inbound recording but no matching `meeting_uid` (an unusual edge case caused, for example, by hand-edited archives), the restore regenerates the conflicting field on the inbound row and renames the extracted audio file rather than aborting the import.

## Playback Proxies

Playback proxy files are not included in backups; they are regenerated asynchronously after restore. Newly restored recordings may briefly show as still processing until their proxy is rebuilt.

## Cross-System Restores

Restoring a backup onto a different installation preserves the original `public_id` of each recording. Companion devices that were paired to the source installation still address recordings by the same `public_id`, but the JWT and pairing records they hold are tied to the source installation and must be re-issued from the new system.

## Recommendations

- Create backups before upgrades.
- Keep at least one offline copy.
- Restrict access to backup archives.
- Test restore procedures before you rely on them operationally.

## Related Docs

- [ADMIN.md](ADMIN.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [CALENDAR.md](CALENDAR.md)
