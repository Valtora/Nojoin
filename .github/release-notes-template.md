## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **Anthropic models work again.** Version 1 of the Anthropic SDK removed the `temperature` parameter, and the 2.5.0 images install that version, so every Anthropic request failed before it left the server. Nojoin no longer sends it (#272).
- **Merging duplicate speakers no longer joins two identified people.** An unnamed third voice that resembled both could bridge them, collapsing one confirmed identification into the other. The check now covers the whole group a merge would create (#270).
- **Assigning a line to a person always shows that person.** The assignment could land on a speaker record that an earlier merge had retired, and the transcript then fell back to the raw diarization label (#270).
- **A code execution flaw in the diarization model loader is closed.** CVE-2026-58659 let a crafted PyTorch Lightning checkpoint run code when loaded. The worker loads pyannote models that ship with Nojoin or come from Hugging Face, so the exposure was a tampered model file. Both Lightning packages move to the fixed 2.6.6 (#288).
- **The API image carries current Debian security fixes.** Its base image lags the Debian archive by weeks, so the image now applies pending system package updates when it is built. This release picks up fixes to gzip, PCRE2, SQLite, Perl and OpenSSL (#291).
- **The notes editor is off a vulnerable tiptap release.** GHSA-cp6q-959q-f8rh in `@tiptap/core` is cleared by moving the whole tiptap family together (#272).
- Routine updates include React 19.3, Next.js 16.3.5 and uvicorn 0.53 (#284, #288, #292).

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
- No change to the example compose file, the environment variables or the nginx configuration. Pulling the new images is the whole upgrade.
- The upgrade does not change recordings whose speakers were already wrongly merged. Reprocessing such a recording runs the corrected merge pass.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Code only. Redeploy the previous image tags.
- Nothing to downgrade, since this release adds no schema revision.
- Rolling back to 2.5.0 brings back the Anthropic failure and the Lightning flaw, so prefer fixing forward.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->

- Anthropic models now run at the provider's default sampling, where Nojoin previously set a low fixed temperature. Notes and titles from Anthropic models can vary more between runs than before. Other providers are unchanged.
- Carried over from 2.4.0. The AI analytics tier spends your own provider quota on every run, is never dispatched automatically, and has no account level cap.
- Carried over. Measured delivery does not refresh itself, so a transcript edited afterwards is reported stale and re-measured only when asked, and overlapping speech is a floor rather than a total.
- Carried over. The 120 second GPU window can be too large when live capture and transcription contend for one card, and the Codex payload in the worker-io image is a stripped static binary that scanners cannot introspect.
- Carried over from 2.5.0. A document batch uploads one file at a time, because the backend admits two concurrent uploads per user and rejects the rest.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- No capture code changed in this release. Supported browsers, operating systems and audio sources are unchanged, and shared-audio capture still resolves on Chromium desktop only.

### Changes

{{CHANGELOG}}
