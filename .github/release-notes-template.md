## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **Long recordings finalize again.** PostgreSQL accepts at most 32767 bind parameters in one statement, and the window manifest write crossed that after about 1h49m of audio, so finalizing a two-hour recording returned a 500. Bulk statements are now batched from the live column count.
- **Finalizing no longer stalls the rest of the API.** Audio concatenation ran inline on the event loop and blocked every other request for its duration, measured at 20 seconds on a two-hour recording. It now runs on a worker thread.
- **Several documents attach in one upload.** The dialog holds a queue, each file carrying its own visual-analysis switch. A failure no longer aborts the batch, and a retry re-sends only what failed.
- **Capture stops reporting outages the browser cannot confirm.** Connection probes are serialised rather than piling up behind a stalled request, a probe that outlasts its own timeout by a wide margin is treated as suspension, and returning to the foreground clears the streak.
- **Floating panels inside modals open where they can be seen.** The date picker, the colour picker and the merge-target search in the People modal were clipped or flipped out of view by the modal they opened in, worst at phone widths. They now position against the window.
- **Note generation recovers on stacks that left `NOJOIN_CODEX_PATH` blank.** Compose puts an empty string in the environment rather than leaving the variable unset, which is a value, so the default path was never reached and note generation failed with a permission error. `NOJOIN_UMASK` had the same shape and logged an invalid value warning at every startup.
- **Prefork worker children are retired after a fixed number of tasks.** A pool child previously lived for the life of its container. This is a backstop that bounds an undetected leak, not a fix for an observed one, and is not expected to lower steady-state memory.
- **The MCP connector is rebuilt on version 2 of the MCP SDK.** The tools it exposes and the grants it accepts are unchanged.

### Upgrade

Pull the new images and recreate the stack:

```bash
docker compose pull
docker compose up -d
```

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- No Alembic revisions in this release. Nothing about the schema changes, and no first-boot migration runs.
- **Optional if you maintain your own compose file.** The parse lane gains `--max-tasks-per-child=25` on its `command:`, copied from [docker-compose.example.yml](https://github.com/Valtora/Nojoin/blob/main/docker-compose.example.yml). Without it that lane falls back to the global limit of 500, which is a looser bound rather than a break. Deploying from the example file needs no change.
- Child recycling has no effect on `worker-gpu`, which runs a solo pool with no children to recycle, and does not disturb the Celery Beat schedule.
- No new service, image or environment variable.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Code only. Redeploy the previous image tags.
- Nothing to downgrade, since this release adds no schema revision.
- MCP grants issued under this release carry scopes the previous release understands, so connectors keep working.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->

- Carried over from 2.4.0. The AI analytics tier spends your own provider quota on every run, is never dispatched automatically, and has no account level cap.
- Carried over. Measured delivery does not refresh itself, so a transcript edited afterwards is reported stale and re-measured only when asked, and overlapping speech is a floor rather than a total.
- Carried over. The 120 second GPU window can be too large when live capture and transcription contend for one card, and the Codex payload in the worker-io image is a stripped static binary that scanners cannot introspect.
- A document batch uploads one file at a time, because the backend admits two concurrent uploads per user and rejects the rest. A large drop therefore takes as long as the sum of its files.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- Supported browsers, operating systems and audio sources are unchanged. Shared-audio capture still resolves on Chromium desktop only.
- The coverage warning is net of audio still queued in the browser, and names the queued part separately. A server that stops answering for two minutes leaves two minutes queued, which was never at risk.
- An outage keeps explaining a shortfall for a few minutes after the connection recovers, because the check runs every fifteen seconds and the queue takes longer than that to drain.
- The live transcript window no longer overflows the card it sits in on a short window.

### Changes

{{CHANGELOG}}
