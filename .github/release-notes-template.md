## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **Backups include every recording's audio.** Browser recordings and AAC, MP4 and WMA imports went into the archive as metadata only, and the export still reported success. Take a fresh backup after upgrading (#332).
- **A failed transcription is reported as a failure.** A GPU out-of-memory run was saved as an empty, completed transcript. The recording now ends in Error with the reason, after one retry with the GPU memory freed (#317).
- **Transcripts keep all their text.** The final merge dropped segments without word timings and fragments shorter than a tenth of a second (#349). Finalize could also fail on a duplicate utterance id when diarization was off or word timings were missing (#344).
- **Notes work with current Claude models.** Claude Haiku 5.5, Sonnet 5.5 and Opus 5.5 think by default, so a reply can open with a thinking block, and Nojoin read only the first block. Notes and the other Anthropic calls failed whenever the model thought first (#363).
- **Ollama refuses a prompt that does not fit its context window.** Ollama cut the start of an oversized prompt without an error, so a long meeting got notes about its last part only. Nojoin now sizes the window to the model and fails the request instead (#343).
- **Video and media files can be imported.** MKV, MKA, MOV, AVI, M4V, TS, MTS, MPEG and 3GP files are accepted and only their audio is kept, so an hour of OBS recording is stored as 76 MB instead of 3.5 GB (#333).
- **Large imports work.** Each uploaded chunk hashed every chunk before it again, so a 3 GB video import froze the server for seconds at a time and slowed as it went, and an import over 2 GiB then failed to finalise. Each chunk is now read once, and files over 2 GiB finalise (#362, #364).
- **Speech detection and speaker separation can be tuned per user** under **Settings > Recording** (#340).
- **New appearance settings** choose the colour palette (Graphite, Ultraviolet or Marigold), the corner style and the density, kept per browser. Dark mode no longer flashes light on a hard reload (#320).
- **The API stays responsive while an AI provider is down.** Listing models from an unreachable Ollama server held up every request for 10 seconds. Validating an unreachable Ollama server now fails instead of reporting a connection (#358, #359).
- **Fewer model downloads.** GPU hosts prepare the Parakeet or Canary weights they load, where they used to fetch the int8 copy and then download another 2.5 to 4 GB on the first transcription (#338). The embedding model stays in the model cache across image updates (#328).
- **Delivery and overlap analytics measure browser recordings** and WebM, M4A, AAC, MP4 and WMA imports. Neither could open these files before (#331).
- **JWT handling moves from the unmaintained python-jose to PyJWT.** Sessions and API tokens issued before the upgrade stay valid (#330). Next.js 16.3.8, sharp 0.35.5 and source-map-js 1.2.2 clear HIGH advisories (#321), and the worker images patch OpenSSL CVE-2026-84782, fsspec CVE-2026-104851, urllib3 and the kernel headers (#361).

### Breaking Changes

- **One transcription engine and model for the whole install.** **Settings > Transcription** saved the choice on the account of the administrator who made it, and every other user ran on the install default. On first start the owner's choice becomes the install's, so a single-user install needs no action. On an install with several users, everyone now transcribes with the owner's engine and model, and a choice saved by another administrator is not carried over (#334).

### Upgrade

Pull the new images and recreate the stack.

```bash
docker compose pull
docker compose up -d
```

- Take a fresh backup once the stack is up. Archives from earlier versions hold browser recordings and AAC, MP4 and WMA imports without their audio.

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- One Alembic revision widens `recordings.file_size_bytes` to a 64-bit integer, so imports over 2 GiB can be finalised. It runs on the first API start and takes moments.
- On first start the API copies the owner's transcription engine and model into `config.json` and clears them from the owner's account. It runs once and needs no action.
- No change to the example compose file, the environment variables or the nginx configuration.
- The backup format is unchanged. Original-quality archives grow by the audio that earlier versions left out, and audio members are now stored without zip compression.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Roll the schema back before the images, because the 2.5.1 API refuses to start against a revision it does not know. With 2.6.0 still running, run `docker compose exec api alembic downgrade f7a2c6d3b418`, then redeploy the 2.5.1 image tags. The downgrade clears the stored size of any recording over 2 GiB.
- 2.5.1 reads the engine setting from `config.json` as the install default, so the owner keeps the same engine.
- Rolling back to 2.5.1 brings back the backups without audio and the empty transcripts after a GPU failure, so prefer fixing forward.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->

- New in 2.6.0. With Ollama, a meeting whose transcript does not fit the context window now fails note generation, where it used to get notes about its end only. Raise the Ollama context window under **Settings > AI providers** if the GPU has room, or use a hosted provider for long meetings. Splitting long meetings into parts is planned.
- Carried over from 2.5.1. Anthropic models run at the provider's default sampling, so their notes and titles can vary more between runs than other providers'.
- Carried over from 2.4.0. The AI analytics tier spends your own provider quota on every run, is never dispatched automatically, and has no account level cap.
- Carried over. Measured delivery does not refresh itself, so a transcript edited afterwards is reported stale and re-measured only when asked, and overlapping speech is a floor rather than a total.
- Carried over. The 120 second GPU window can be too large when live capture and transcription contend for one card, and the Codex payload in the worker-io image is a stripped static binary that scanners cannot introspect.
- Carried over from 2.5.0. A document batch uploads one file at a time, because the backend admits two concurrent uploads per user and rejects the rest.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- No capture code changed in this release. Supported browsers, operating systems and audio sources are unchanged, and shared-audio capture still resolves on Chromium desktop only.

### Changes

{{CHANGELOG}}
