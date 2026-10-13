## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: one bullet per item, one or two sentences each. What an operator would notice, not how it works. Detail belongs in the docs. Remove the section if a release has nothing to lead with. -->

- **The GPU worker runs on CUDA 13 and supports RTX 50 series cards.** It needs NVIDIA driver 580 or newer and a Turing or later GPU, so GTX 900 and 10 series, TITAN V, Tesla P4, P40 and P100 and V100 cards are no longer supported. On one of those, or on an older driver, the GPU worker logs why when it starts and processes on the CPU instead of failing each recording (#414).
- **Reprocessing gives the same Whisper transcript.** Whisper re-decodes a hard passage by sampling, and that sampling is now seeded, so a reprocess no longer changes words in crosstalk or noise (#414).
- **Long meetings get notes with Ollama.** When a transcript does not fit Ollama's context window and no secondary provider takes it, the notes are written in parts and combined, and Meeting Chat answers from the meeting's most relevant excerpts (#394).
- **Import Audio is on the recordings list**, beside Start Meeting, instead of in the navigation rail (#393).
- **The log viewer shows `worker-parse`** (#404).
- **Dependency updates.** torch moves to 2.14.1, which fixes GHSA-rrmf-rvhw-rf47 (#405). onnxruntime-gpu moves to 1.31.0, the worker's pyannote.audio to 4.0.7, Next to 16.4.0 and FastAPI to 0.143.0, and the worker image is about 1 GB smaller (#414). ONNX Runtime's own telemetry, on by default in its Linux builds since 1.29, is turned off (#415).

### Upgrade

Check the GPU before pulling. A GPU host needs NVIDIA driver 580 or newer and a card of compute capability 7.5 or higher.

```bash
nvidia-smi --query-gpu=name,compute_cap,driver_version --format=csv
```

Update the host driver first if it is older than 580. Then pull the new images and recreate the stack.

```bash
docker compose pull
docker compose up -d
```

- A card below compute capability 7.5 still runs 2.8.0, with processing on the CPU. The worker log has a `[gpu-preflight]` line naming the card. Stay on 2.7.0 to keep GPU processing on it.
- CPU-only deployments need nothing new.

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. Keep it to bullets. -->

- No database migration in this release.
- No change to `.env`, the example compose file or the nginx configuration.
- The backup format is unchanged.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

- Rollback is code-only. Redeploy the 2.7.0 image tags, since the schema has not changed.
- 2.7.0 runs on CUDA 12.6, which supports pre-Turing cards and drivers from 525.

### Known Issues

<!-- Maintainer: list only problems Nojoin can fix, each with its open issue. A limit set by a dependency or a provider is documented in the guide for that feature and does not belong here. -->

- None known.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

- Supported browsers, operating systems and audio sources are unchanged. The live waveform reads the capture level through a store subscription instead of component state (#404).

### Changes

{{CHANGELOG}}
