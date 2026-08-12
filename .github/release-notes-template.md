## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **Meeting analytics.** A new Analytics tab on every recording covering talk share, turn structure, reply time and measured overlapping speech. Optional tiers measure vocal delivery from the audio and run a single AI pass over the transcript on request.
- **The MCP connector becomes agentic.** Thirty-two tools covering semantic search, recording organisation, reprocessing, transcript corrections, tasks and calendar. Nothing in it deletes permanently.
- **A cross-user fix in meeting chat.** On a multi-user instance, a supplied tag id could pull another user's chunks into chat context. Retrieval is now constrained to the caller's own recordings.
- **A reworked first-run wizard** offering three AI routes: the server's provider credential, your own Claude or ChatGPT subscription, or no AI for now.
- **Honest reporting of lost capture audio.** The shortfall is measured on the server rather than estimated, names a cause only where there is evidence for it, and can be dismissed. A pre-flight notice covers Chrome Memory Saver before a meeting starts.
- **API stall detection.** Event-loop lag is reported to the container log with Pressure Stall Information naming the cause.
- **Seven documented environment variables now reach the containers.** One manual step for operators with their own compose file, under Migration.
- **A denser live workspace**, bounded against the window, and notes tables without their unused ID columns.
- **A new site at www.nojoin.co.uk**, replacing the GitHub Pages site.

### Upgrade

Pull the new images and recreate the stack:

```bash
docker compose pull
docker compose up -d
```

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- Five Alembic revisions, all automatic. None touches recordings, transcripts or notes. One removes the retired companion app notice and the flag that tracked it.
- **Action needed if you maintain your own compose file.** Seven documented variables previously never reached the process. Copy them from [docker-compose.example.yml](https://github.com/Valtora/Nojoin/blob/main/docker-compose.example.yml): `OLLAMA_CONTEXT_WINDOW`, `SECONDARY_OLLAMA_CONTEXT_WINDOW`, `BACKUP_EXPORT_DIR`, `NOJOIN_UMASK` and `NOJOIN_TELEMETRY_ENDPOINT` on the shared anchor, `MCP_ANONYMOUS_DISCOVERY` on the api service, and `NOJOIN_CODEX_PATH` on the worker lanes. Deploying from the example file needs no change.
- Analytics are not backfilled. The derived figures are available on every existing meeting immediately; the delivery and AI tiers start pending and run when asked, per recording.
- The Ollama context default drops from 131072 to 32768, which fits a 16 GB card and still covers a two-hour meeting. New installs only, since the value is persisted on first start.
- The notes template version moves to 2. A template forked from the old structure is reported stale and can be reset in Settings.
- No new service, image or dependency.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Code only. Redeploy the previous image tags.
- Downgrading the schema as well discards stored delivery and AI analytics, which are regenerated on request after a later upgrade.
- MCP grants issued under this release carry scopes the previous release understands, so connectors keep working.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->

- The AI analytics tier spends your own provider quota on every run and is never dispatched automatically. There is no account level cap.
- Measured delivery does not refresh itself. A transcript edited afterwards is reported stale and re-measured only when asked.
- Overlapping speech is a floor rather than a total. It underestimates, never the reverse.
- Carried over: the 120 second GPU window can still be too large when live capture and transcription contend for one card, and the Codex payload in the worker-io image is a stripped static binary that scanners cannot introspect.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- Supported browsers, operating systems and audio sources are unchanged. Shared-audio capture still resolves on Chromium desktop only.
- The shortfall warning is measured on the server, classifies its cause, and can be dismissed.
- A pre-flight notice names the Chrome Memory Saver setting before a meeting starts, dismissed per browser. A screen wake lock is held for the duration of a capture.
- An interrupted capture that banked no audio is discarded rather than raising a resume-or-discard prompt.

### Changes

{{CHANGELOG}}
