# Nojoin Backup and Restore Guide

Nojoin includes a full-system backup and restore flow under **Settings > Backup & Restore**.

This guide explains what is included, what remains redacted, and what should be treated as sensitive during handling.

## What a Backup Contains

A backup archive can include:

- Database records.
- Dashboard state such as Task Cards.
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

## Recommendations

- Create backups before upgrades.
- Keep at least one offline copy.
- Restrict access to backup archives.
- Test restore procedures before you rely on them operationally.

## Related Docs

- [ADMIN.md](ADMIN.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [CALENDAR.md](CALENDAR.md)
