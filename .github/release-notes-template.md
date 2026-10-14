## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **Long browser recordings keep up with the meeting.** Each two-second segment is now registered in constant time, so uploads no longer fall behind after about 100 minutes, and stopping a long meeting no longer sits on Paused while a backlog drains (#418).
- **Browser capture has no gaps and no loss.** The browser cuts exact two-second segments from the audio stream and uploads them as FLAC, instead of restarting its recorder for each segment, which dropped about 28 ms every time. A recording's stored length now matches its audio. New browser recordings are kept as FLAC, and existing ones stay WebM (#418).
- **Unsent audio survives a closed tab.** Segments waiting to upload are kept in the browser until the server has them, and resuming or choosing **Stop and process** sends them first. While stopping, the controls say **Uploading** with the time left (#418).
- **Exports can include analytics and open on a Nojoin letterhead.** Choose any mix of notes, analytics and transcript. PDF and Word files carry the logo and Geist type, keep each speaker's colour, and give dates in the reader's timezone. An admin can turn the letterhead off under **Settings > Appearance** (#421).
- **PDF exports no longer leave grey bars on the pages after a table** (#421).
- **Release images are built on the maintainer's release host.** The release workflow still signs them, so the cosign verify command is unchanged. The provenance attestation now records that build rather than a GitHub runner (#420).
- **Dependency changes.** The API image drops markdown-pdf, PyMuPDF and plantuml, and adds soundfile, which decodes capture segments (#418, #421).

### Upgrade

Pull the new images and recreate the stack.

```bash
docker compose pull
docker compose up -d
```

- Reload any open Nojoin tab after upgrading so browser capture moves to FLAC segments. A tab still running the old page keeps working, because its WebM segments are still converted.
- Browser capture now uploads about 150 kbit/s with one active audio source and about 300 kbit/s with two, against 160 kbit/s before. A remote user on a slow uplink may see the uploads-behind notice sooner.

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- One Alembic revision adds the `recording_audio_chunk_cursors` table, which tracks each live recording's capture progress. It runs on the first API start and takes moments. A browser recording in progress during the upgrade rebuilds its cursor on its next segment.
- No change to `.env`, the example compose file or the nginx configuration.
- The backup format is unchanged. Browser recordings go into archives as FLAC, or as WebM for those made before this release.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Roll the schema back before the images, because the 2.8.0 API refuses to start against a revision it does not know. With no browser recording in progress and 2.9.0 still running, run `docker compose exec api alembic downgrade c4d8e1f2a3b5`, then redeploy the 2.8.0 image tags. The downgrade drops only the capture cursor table.
- Recordings made on 2.9.0 stay FLAC, which 2.8.0 already stores and plays for imports. Reload open Nojoin tabs after rolling back.

### Known Issues

<!-- Maintainer: list only problems Nojoin can fix, each with its open issue. A limit set by a dependency or a provider is documented in the guide for that feature and does not belong here. -->

- None known.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- Supported browsers, operating systems and audio sources are unchanged. Capture now runs through an AudioWorklet, which every supported browser provides.
- Audio that has not yet uploaded is stored in the browser's IndexedDB for the Nojoin site until the server acknowledges it, the recording is discarded, or it is finalised. Clearing the site's data in that window loses those segments (#418).

### Changes

{{CHANGELOG}}
