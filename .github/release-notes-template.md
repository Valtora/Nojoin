## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: lead with what an operator would notice. Remove the section if a release has nothing to lead with. -->

GPU transcription was never running on the GPU. The PyTorch base image ships CUDA and cuDNN as wheels that are absent from the loader cache, so ONNX Runtime could not resolve cuDNN, dropped the CUDA execution provider, and built every ASR session on the CPU without reporting an error. A 40 minute recording that took roughly 50 minutes now takes 2 minutes 50 seconds. If your worker has been saturating several cores while nvidia-smi showed an idle card, this is why.

A paused capture can now be stopped and kept. The resume-or-discard prompt previously offered no outcome that preserved the audio, because resuming reopens the browser share picker and discarding destroys the recording. A meeting that has already ended now has a correct choice.

A recording orphaned by a worker that died mid-pipeline is recovered on the next worker start. Previously it stayed in PROCESSING with no route back, because the startup sweep looked only at QUEUED and the reprocess endpoint refuses a processing recording.

Codex-backed generation on the worker-io image failed with a permission error. The data directory permission repair followed symlinks, and the Codex CLI stores dispatch links on the persistent volume, so a privileged pass re-permissioned the bundled codex binary itself.

The interface renders in Geist. The font was being downloaded on every page load and then applied to almost nothing, because a leftover create-next-app rule set the body font to Arial.

### Upgrade

Pull the new images and recreate the stack:

```bash
docker compose pull
docker compose up -d
```

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. -->

This release adds no Alembic revision, so there is no schema migration to apply and no longer first start.

GPU operators should expect one extra step on first use. The ASR engines now load fp32 weights where a GPU is present and int8 weights only where one is not, because ONNX Runtime has no CUDA kernels for most quantized operations. The first transcription after upgrading therefore downloads a different set of weights for the configured model into the shared model cache, and later runs reuse it. Canary 1B needs roughly 5.4 GB of VRAM with those weights, and the transcription window is capped at 120 seconds on a GPU host (240 seconds on CPU) so an 8 GB card does not overflow.

The API image moves from Python 3.14 to 3.12, matching the worker lanes, CI, and the documented prerequisite. That is internal to the image and needs no action.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->
Rollback is code only. Redeploy the previous image tags. The range carries no schema change, no configuration change, and no new environment variable. The int8 weights stay in the model cache, so a rolled-back worker needs no re-download.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->

The known issue published with 2.1.0 is resolved. Every Celery dispatch reachable from a request handler now runs off the event loop through a single helper, so an unreachable Redis no longer delays the request that triggered it, and a guard test fails the build on any dispatch reintroduced inside an async function.

Two operational notes remain. If live capture and a transcription job contend for the same card, the 120 second GPU window may still be too large, and lowering it is currently a source change (GPU_MAX_CHUNK_DURATION_S in the ONNX ASR engine) rather than a setting. Separately, the Codex payload in the worker-io image is a stripped static binary that vulnerability scanners cannot introspect, so that image passes the release scan because there is nothing to examine rather than because its contents were examined. Operators who need a fully audited image should run worker-io for the Claude path only, or skip the lane.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

Supported browsers, operating systems, and audio sources are unchanged. Capture behaviour changes in three ways.

A paused recording can be finalised directly, without a resume round trip first, and stopping now works with no browser runtime attached. Every stage on the way to finalise is bounded, so a recorder that never reports stopping can no longer wedge the control surface, and a stop can never settle in a state that disables every control.

A badge reports captured duration when it falls behind the wall-clock timer, and a watchdog force-rolls a segment chain that has gone quiet.

The capture guide now documents a limitation that was previously understated. A tab that the browser or operating system suspends carries on recording nothing, and that audio cannot be recovered afterwards. Measured against Chromium, a page driven to the frozen lifecycle state retained 52.4% of its audio across a 20 second freeze, while reporting itself as recording throughout. Two troubleshooting entries name the browser and operating system settings responsible.

### Changes

{{CHANGELOG}}
