## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **Automatic speaker merges no longer hand one person's lines to another.** A cluster joins a merged speaker only when every pair across the two clears the threshold, so a short cluster of backchannel lines can no longer bridge two people (#436).
- **Automatic merges can be undone.** The speaker panel lists each cluster processing merged into a speaker, with an **Undo merge** button that gives its lines back. Recordings processed before this release need **Retry Processing** instead (#436).
- **A cluster joins a known person only if it sounds like them.** Merging into a speaker identified from the People library also needs the cluster to score at least 0.60 against that person's saved voiceprint (#451).
- **Parakeet and Canary lose less speech.** Both transcribe in windows of at most 30 seconds that close at a pause, and skip silent windows. Parakeet now loads its fp32 weights on a CPU too, because the int8 encoder dropped stretches of clear speech. The GPU transcription window setting is gone (#449).
- **Workers with a CPU limit run at full speed.** ONNX Runtime now sizes its threads to the worker's CPU limit instead of the host's core count, which had made Parakeet 2.3 times and Canary 3.6 times slower in a 6-CPU container. Text embedding uses the same count, and CPU diarisation is no longer held to one thread once the speech detector has loaded (#444, #447, #450).
- **Opening a meeting no longer rewrites its notes.** The notes editor saved notes it had only loaded, which rewrote them in its own Markdown and added an undo step with no edit made (#463, #465).
- **A full calendar sync keeps recording links.** It used to delete and re-insert every event, which unlinked every recording from its calendar event. Links lost before this release are not restored (#454).
- **The last backup is kept and reused.** Nojoin keeps the most recent finished backup on the server and downloads it again while nothing in it has changed. A new backup builds in the background, and a notification offers **Download Now** when it is ready (#453).
- **Owners and admins are told when an update is out.** A notification offers **Go to Releases**, **Skip Update** and **Remind Me**. Notifications no longer let the page show through in dark mode (#440).
- **Settings are reorganised.** Every page uses the same rows and controls, and settings that apply to the whole installation or only to this browser are marked. The notes language and automatic analysis moved to **Notes**, the Meeting Edge switch and model to **AI providers**, and the daily analysis limit to **Users** (#458, #459).
- **A newly keyed AI provider can get its first model from settings.** System health no longer reports a provider with a key but no model as configured, and release notes in **Settings > Updates** render properly (#456, #457).
- **A read-only public demo** at demo.nojoin.co.uk shows six public-domain NASA briefings and rebuilds itself on each release (#464, #466).
- **Dependency changes.** The backend moves to SQLAlchemy 2.1, the workers use psycopg 3 in place of psycopg2, and the images no longer install libpq (#441).

### Upgrade

Pull the new images, stop the stack, and start it again.

```bash
docker compose pull
docker compose down
docker compose up -d
```

- Stop the stack before starting the new images. A 2.9.0 worker still running after the migration fails every final pass, because it writes to a table this release drops (#437).
- A host using Parakeet or Canary downloads weights again in the model preparation at first start. Copies that landed in Hugging Face's shared store are fetched again, about 2.5 GB for Parakeet and 4 GB for Canary, and a CPU host running Parakeet downloads its fp32 weights, about 2.5 GB (#448, #449). Reprocess a recording to transcribe it again with the shorter windows.
- The saved backup stays in `data/backups` until a newer one replaces it or an administrator deletes it, so allow disk for one archive, and for two while a new one builds (#453).

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- Two Alembic revisions run on the first API start. One keeps the diariser's label on each utterance, which **Undo merge** needs. The other drops `recording_asr_window_results`, a log of transcription attempts that nothing read (#436, #437).
- Two settings are retired. A stored `enable_asr_window_result_ledger` or `gpu_asr_window_s` is ignored and dropped from `config.json` on the next save, and the `ENABLE_ASR_WINDOW_RESULT_LEDGER` environment variable is ignored (#437, #449).
- No change to `.env`, the example compose file or the nginx configuration.
- The backup format is unchanged.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Roll the schema back before the images, because the 2.9.0 API refuses to start against a revision it does not know. With no browser recording in progress and 2.10.0 still running, run `docker compose exec api alembic downgrade d7e3a1b9c5f2`, then stop the stack and start the 2.9.0 image tags.
- The downgrade recreates the dropped table empty and removes the diariser labels, so **Undo merge** has nothing to offer for meetings processed on 2.10.0 if you upgrade again.

### Known Issues

<!-- Maintainer: list only problems Nojoin can fix, each with its open issue. A limit set by a dependency or a provider is documented in the guide for that feature and does not belong here. -->

- Regenerating notes on several recordings at once can fail some of them with "No model selected". Regenerating each one on its own works (#468).

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- Supported browsers, operating systems and audio sources are unchanged.
