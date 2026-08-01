## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: lead with what an operator would notice. Remove the section if a release has nothing to lead with. -->

#### A Flat Design System

The interface has been rebuilt on a flat design system. Gradients, glass panels and backdrop blur are gone from both the app and the marketing site, replaced by semantic tokens and a shared primitive set covering buttons, cards, badges, inputs, selects and one modal. Roughly twenty five hand rolled modal scrims now come from that single component, so they share a focus trap, a scroll lock and a height cap with a viewport gutter, which stops a tall dialog on a phone pushing its own actions below the fold. A contrast script measures 136 declared token pairings across both themes and runs in the lint job, so a contrast regression fails the build rather than surviving until someone squints at a screenshot.

#### Denser Layouts and Real Touch Targets

The same pass recovered a great deal of vertical space. Cards no longer nest inside cards, the dashboard and settings surfaces move onto a denser layout, and the live recording workspace is reorganised around a capture toolbar with two wide columns, so the transcript and Meeting Edge stop each paying for a third of the page. Touch handling improves alongside it. The smallest icon buttons now render a 16px glyph inside a 40px box, and row actions that were revealed on hover alone, which put them out of reach on a touch device, are shown outright below the desktop breakpoint.

#### Documents Are Parsed Visually

Uploaded documents are parsed visually and now reach notes generation as well as meeting chat. Parsing covers PDF, PowerPoint, Word, Excel, CSV, text, Markdown and images, with a structural pass on the Office formats so slide titles, tables, speaker notes and native chart values come back exactly as they were authored rather than estimated from a rendered picture. Behind that, page images go to a vision capable model on Anthropic, OpenAI, Gemini, Ollama and both subscription CLI paths. Where no vision model is reachable, a local OCR tier runs on your own server, costs nothing and sends nothing anywhere, so a scanned page stays searchable on an install with no AI configured at all. Pages are written as each one completes, so an interrupted parse resumes rather than repeating vision calls that were already paid for. The upload ceiling rises from 20 MB to 250 MB.

#### Parsing Runs on Its Own Lane

Parsing runs on its own Celery lane. A parse has no page cap, so one large upload can hold a worker slot for a long time, and on the io lane that would sit beside Meeting Edge and meeting chat and degrade a live meeting. The lane reuses the worker-io image, so it adds no build and no new image to scan, but it does have to be added to your compose file. See Migration below.

#### A New Embedding Model

Search and meeting chat move to a new embedding model, which purges the existing index. This is the one change in the release that needs an operator action, and it is covered in full under Migration.

#### CPU Only Deployments No Longer Crash

A CPU only deployment no longer dies during transcription. The ONNX ASR engine asked onnxruntime for the CUDA execution provider unconditionally, and onnxruntime-gpu does not degrade when no device is present. It loads its CUDA provider library, finds nothing, and takes the process down with SIGSEGV, which raises no Python exception, so nothing downstream could catch it and fall back. The provider list is now gated on the same device node probe already used to choose quantisation. The CPU only deployment documented in the deployment guide, which drops the compose deploy block while keeping the same image, is the exposed shape exactly.

### Upgrade

Pull the new images and recreate the stack:

```bash
docker compose pull
docker compose up -d
```

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. -->

This release adds three Alembic revisions. Take the backup before upgrading rather than after, because one of them is destructive and its downgrade is destructive in the same way.

**The embedding cutover deletes every existing vector.** The search and meeting chat embedding model changes from all-MiniLM-L6-v2 to jina-embeddings-v2-small-en. The old model truncated its input at roughly 256 tokens, so the tail of any real document page was never searchable, while the new one has an 8192 token window, which makes a whole page a single retrieval unit. Vectors from two models are not comparable at any width, so there is no arithmetic that converts one to the other. The revision widens context_chunks.embedding from 384 to 512 dimensions and deletes every row. Semantic search and meeting chat return nothing until the index is rebuilt.

Rebuilding is dispatched by a sweep that re indexes up to 50 recordings per call and skips anything already at the current version. It is idempotent, so run it repeatedly until it reports zero.

```bash
docker compose exec worker-parse \
  celery -A backend.celery_app.celery_app call \
  backend.worker.tasks.rebuild_text_embeddings_task
```

Re-indexing is local inference and costs nothing. Documents uploaded before this release have no stored pages, so the sweep re parses them structurally, never visually, and no provider quota is spent without being asked. Use **Parse again** on a document to run visual analysis on one deliberately. The new model is roughly 120 MB and downloads to the shared model cache the first time a worker embeds anything, so the first rebuild call on an air gapped host fails until that cache is populated.

**A worker-parse service has to be added.** Copy it from [docker-compose.example.yml](https://github.com/Valtora/Nojoin/blob/main/docker-compose.example.yml). It runs the worker-io image, takes the same environment as the other worker lanes, needs no GPU, and carries no beat schedule (worker-io owns that). Without it, uploads queue for parsing and nothing picks them up.

The worker image gains the tesseract binary and its English language data for the OCR tier, roughly 15 MB. There is no new environment variable and no other configuration change.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

Rollback is not code only this release. The previous images cannot read a 512 dimension embedding column, so redeploying them alone leaves search and meeting chat broken, and the Alembic downgrade deletes every vector again for the same incomparability reason. Restore the database backup taken before the upgrade, then redeploy the previous image tags. Leaving the worker-parse service in place is harmless, since the previous release simply queues nothing to it, and the cached embedding models are harmless to leave too.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->

Documents uploaded before this release keep their text only extraction. The rebuild sweep re parses them structurally rather than visually, deliberately, so upgrading spends no provider quota unasked. Running **Parse again** on a document is the way to send an older upload through visual analysis, one at a time.

A visual parse spends provider quota per page and there is no account level cap on it. The upload modal warns above 20 MB and the visual analysis toggle can be turned off per upload, but a long document is a long bill.

Two notes carry over from 2.2.0. If live capture and a transcription job contend for the same card, the 120 second GPU window may still be too large, and lowering it is a source change (GPU_MAX_CHUNK_DURATION_S in the ONNX ASR engine) rather than a setting. And the Codex payload in the worker-io image is a stripped static binary that vulnerability scanners cannot introspect, so that image passes the release scan because there is nothing to examine rather than because its contents were examined. That image now backs two lanes rather than one, since worker-parse reuses it, so an operator who needs a fully audited image should weigh both. Running the Claude path only, or skipping the io lane, still leaves parsing available through a hosted provider or local OCR.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

Supported browsers, operating systems and audio sources are unchanged, and capture behaviour itself is unchanged. The workspace around it was rebuilt.

The capture console is now a toolbar across the top rather than a card competing with the panels beside it, and the workspace below it is two columns rather than three, since everything on the surface is dense prose and Meeting Edge subdivides again internally. The live transcript is sized against the viewport rather than against whatever its neighbour happens to be doing, so it no longer runs several screens tall on a busy meeting and pushes the notes off the fold. Meeting Edge's sections fold, and its collapsed sections are unmounted rather than hidden so they cost no height. Attach Docs moves into the toolbar, where it was previously the least findable control on the page despite its useful window closing when processing finishes.

A documents panel now appears alongside the live transcript and notes while a meeting is recording or processing. The point is timing rather than convenience. Notes are generated once, at the end of processing, so a deck attached during the meeting is normally parsed in time to reach them on the first pass instead of marking them stale afterwards. The panel is read mostly by design, with delete and re parse left to the Documents tab, since a destructive control beside a live recording is the wrong thing to offer mid meeting.

Native controls render correctly in dark mode. Select popups, checkboxes, radios, range thumbs and date pickers were drawn light on a dark page, because color-scheme was declared on exactly one date input and the hand rolled selects carried a 3% white fill that composites to an essentially white popup.

### Changes

{{CHANGELOG}}
