## Nojoin {{VERSION}}

Container images for this release. All images are cosign-signed and ship build-provenance and SBOM attestations; verification steps are in the [deployment guide](https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md#verifying-an-image-before-deploying). Pin to a digest for reproducible deployments.

{{IMAGE_DIGESTS}}

### Highlights

<!-- Maintainer: lead with what an operator would notice. Remove the section if a release has nothing to lead with. -->

#### Meeting Analytics

Every recording gains an Analytics tab reporting how the meeting actually went. The first tier covers talk time share and a talk share timeline, turn structure, turn-taking with reply time, and measured overlapping speech. It is derived from the canonical transcript on every read rather than stored, so every meeting already in your library has analytics immediately, with no migration, backfill or reprocess, and a speaker rename, merge or transcript correction is reflected the next time the tab is opened.

A second tier measures vocal delivery from the recording's own audio, covering speaking pace, pitch height and how far it moves, loudness and its range, and pausing within a speaker's own turns. It holds no model, estimating pitch by autocorrelation over numpy and soundfile rather than learning it, and runs on the CPU lane so it never holds the single-slot GPU lane. Pitch movement is reported in semitones rather than hertz, because the same expressive range measures about twice as wide on a high voice as on a low one.

A third tier is optional and spends one AI call over the finished transcript, reporting the topics a meeting moved through and who drove each, which questions went unanswered, and who owned each decision. The parser enforces its own safety rules rather than trusting the prompt. Speaker names are an allowlist, every claim about a person needs a citation that verifies against the transcript actually sent, uncited items are dropped, and everything discarded is counted so a thin result is distinguishable from a quiet meeting.

There is no emotion model here and none should be added. Every measured figure is an arithmetic property of the waveform, and every threshold, band and claim on the surface is sourced in a new evidence note ([ANALYTICS_EVIDENCE.md](https://github.com/Valtora/Nojoin/blob/main/docs/ANALYTICS_EVIDENCE.md)), which also labels what remains judgement. Interruption counts were removed rather than fixed, because one mixed audio channel cannot support the claim. Where speaker attribution is doubtful the surface withholds the number, says why, and routes to the speaker panel, where correcting it updates the analytics immediately.

#### The Connector Becomes a Full Agentic Interface

The built-in MCP connector grows from a read-only view into thirty-two tools that operate the product. Semantic search across every transcript and document with recording, timestamp and page provenance, recording organisation (rename, tag, archive, bin, restore), pipeline operations (reprocess, regenerate notes), document attachment, transcript corrections, the task workspace, calendar listing and linking, and the analytics above. Every tool delegates to the same endpoint coroutine the web client uses, so ownership checks and behaviour cannot drift between surfaces.

Nothing in the connector deletes permanently. The strongest deletion verb is moving a recording to the bin, which is reversible, and emptying the bin exists only in the web app, where mass permanent deletion is a deliberate human act. Scopes stay at two tiers, mcp:read and mcp:write, with write now covering all recoverable mutations. Transcript utterances are paged and carry a revision cursor, mutation responses echo that cursor so a write is self-verifying without a follow-up read, and edits made over MCP are stamped with their source so an assistant's corrections stay distinguishable from a person's in the utterance event log. A beat-scheduled sweep backfills transcripts that were never indexed, so semantic search converges over an existing library without operator action.

Anonymous discovery of the handshake and tool list is on by default and controlled by MCP_ANONYMOUS_DISCOVERY, which is what lets a client that cannot handshake while unauthenticated, Codex Desktop being the live case, begin the OAuth flow at all. [MCP.md](https://github.com/Valtora/Nojoin/blob/main/docs/MCP.md) carries connection instructions for it.

#### A Cross-User Boundary in Meeting Chat

Tag-scoped cross-meeting chat widened vector retrieval to every recording carrying one of the client-supplied tag ids, with no ownership constraint on that subquery. On a multi-user instance a caller could pass another user's tag id and pull that user's transcript and document chunks into their own chat context. The widening subquery now joins recordings and filters on the calling user, so a supplied tag id can only ever reach the caller's own recordings. Single-user installs were never exposed.

#### A First-Run Wizard With Three AI Routes

The wizard now creates the owner account partway through rather than at the end, reordering the steps to terms, transcription, account, AI, finish. Every step after account creation runs authenticated, which is what lets the AI step connect a Claude or ChatGPT subscription in place, since that route is per-user and needs a session. Model preparation is queued at account creation and downloads while AI is configured rather than behind its own progress bar.

The single "AI Provider Configuration Missing" dead end is replaced by three explicit routes: the server's own provider credential, the operator's Claude or ChatGPT subscription, or no AI for now. Running without a provider is a supported configuration rather than a failure, so the step says what works today and what waits. An install running the shipped .env is no longer told its Gemini key is missing for a provider nobody chose.

#### Honest Reporting of Lost Capture Audio

The live view's warning that audio was being lost was wrong in both halves. The figure was estimated from the last sequence number times the timeslice, which under-counts by around 10% over an hour, enough on its own to raise the warning on a healthy recording. It is now summed from the transcoded segments on the server and polled alongside the elapsed clock, and with an honest figure the threshold drops from 10% to 2%. The badge also asserted that the tab was being suspended whatever the cause, sending people to check Chrome settings during a server-side outage. It now classifies the cause, names an unreachable backend or a suspended tab only when there is evidence for it, and can be dismissed, remembering what it was showing so a worsening problem can raise it again.

Tab suspension is now also warned about before a meeting rather than after, when there is still something to do about it. The Meet Now card names the exact Chrome setting and renders the address to add with a copy button. Nojoin takes a screen wake lock for the duration of a capture and re-takes it on return to the foreground, which covers a closed lid and a blanked display and nothing else. No web API lets a page opt out of Memory Saver or read whether it is on, so the rest has to be guidance.

#### Stall Detection in the API

A live recording is the one workload where a two-minute stall cannot be recovered from, because the browser keeps counting wall-clock time while nothing reaches the server. Nojoin had no signal for it, and diagnosing an outage meant reconstructing the freezes from nginx access logs. A watchdog now measures event-loop lag as the overshoot on a fixed sleep, which detects the freeze from inside whatever its cause, and reads Pressure Stall Information at the moment lag is detected to name that cause. Nothing is sampled on a schedule, so a quiet system stays quiet, and sustained pressure is rate-limited so the moment it started is not buried. It is on by default and controlled by NOJOIN_STALL_WATCHDOG_ENABLED.

#### Documented Settings Now Reach the Containers

Seven environment variables were documented for operators and read by the code, but no compose service passed them in, so the stack started, the application default applied, and the operator got no signal their setting had been discarded. All seven are now plumbed through at the scope each one needs, and a regression test asserts every key in the config manager's override map appears in both deployment templates, so the same drift fails CI rather than shipping. Operators running their own compose file have one edit to make, covered under Migration.

#### A Denser Live Workspace

The live recording view is bounded against the window rather than against whatever its neighbour happens to be doing. Meeting Edge scrolls inside its own cell, the absorbing module follows the state (the transcript while recording, the notes editor once it stops), and the grid row no longer takes its height from the tallest content on the page, which was leaving dead space between the transcript and the notes below it. The decisions and actions tables in generated notes lose their ID columns, which numbered rows nothing referenced and cost a column of width on every surface that renders notes.

#### A New Site

nojoin.io replaces the GitHub Pages site, built with Astro and deployed on Cloudflare, with a landing page, a comparison page, a managed-service page, real screenshots in both themes and a theme toggle. The documentation path is served by a scoped Worker script so existing links keep working.

### Upgrade

Pull the new images and recreate the stack:

```bash
docker compose pull
docker compose up -d
```

### Migration

Database migrations run automatically on the first API start after upgrading. Back up your instance before upgrading.

<!-- Maintainer: note any blocking first-boot migration, longer startup, or manual step. -->

This release adds five Alembic revisions. None of them touches your recordings, transcripts or notes, and none needs an operator action. Two add the analytics columns, two add the edit-source columns behind assistant-attributed corrections, and one deletes the retired companion app notice tasks and drops the per-user flag that tracked their delivery. That notice announced the move to browser capture in the 2026-05-26 release and had nothing left to tell anyone, while a fresh install was still handing it to its own first owner.

**Analytics start pending and are not backfilled.** The derived tier needs nothing, so it is available on every existing meeting the moment you upgrade. The delivery tier reads the recording's audio and the AI tier spends your own provider quota, so both start pending on every existing transcript and are produced when you ask for them per recording. Sweeping every library's audio at upgrade time, or spending every user's AI quota on meetings they may never open, is not a cost to impose silently.

**Operators with their own compose file should copy seven environment entries.** If you deploy from [docker-compose.example.yml](https://github.com/Valtora/Nojoin/blob/main/docker-compose.example.yml) there is nothing to do. If you maintain your own file, these are the variables that previously never reached the process, with the scope each one belongs at:

- Shared by the API and the workers: `OLLAMA_CONTEXT_WINDOW`, `SECONDARY_OLLAMA_CONTEXT_WINDOW`, `BACKUP_EXPORT_DIR`, `NOJOIN_UMASK`, `NOJOIN_TELEMETRY_ENDPOINT`. `BACKUP_EXPORT_DIR` in particular has to match on both sides, because a worker writes the export and the API streams it back.
- On the api service: `MCP_ANONYMOUS_DISCOVERY`.
- On the worker lanes: `NOJOIN_CODEX_PATH`.

**The Ollama context default drops from 131072 to 32768.** The old value was sized for a hosted provider. A KV cache costs roughly 190 KiB per token on a 14B model, so 131072 needed about 24 GB of cache on its own and would not load on any consumer GPU, which is the hardware this project targets. 32768 is about 6 GB, fits alongside quantised weights on a 16 GB card, and still covers a two-hour meeting. The value is persisted on first start, so this changes new installs only. Existing installs keep whatever they already resolved, and can change it in Settings or through the variable above.

**The notes template version moves to 2.** The decisions and actions tables lost their ID columns, so a notes template forked from the old structure is reported as stale and can be reset from Settings.

No new service and no new image this release. There is no new Python dependency behind the analytics tiers, and the overlap pass reuses the segmentation model already bundled for diarisation, so nothing new downloads on first use.

### Rollback

<!-- Maintainer: state whether rollback is code-only or requires data steps. Default below. -->

Rollback is code only. Redeploy the previous image tags. The five revisions downgrade cleanly, and none of them is destructive to anything the previous release can read.

Two things to expect if you also downgrade the schema rather than only the images. Stored delivery and AI analytics payloads go with their columns, and are regenerated on request after a later upgrade rather than lost permanently. The companion app notice tasks are deliberately not recreated, since the column would come back as a delivery record for a notice that no longer exists in any released code path. MCP grants issued under this release carry the same two scopes the previous release understands, so connectors keep working across a rollback.

### Known Issues

<!-- Maintainer: list known issues affecting this release, or leave the default. -->

The AI analytics tier spends one long call against your own provider quota every time it is run, and there is no account level cap on it. It is never dispatched automatically for that reason. Where no provider is configured the tier reports itself unavailable, which is a normal state on a healthy install rather than an error.

Measured delivery does not refresh itself. It is stored with the transcript watermark it was measured against, so a transcript edited afterwards is reported stale and is re-measured only when asked, because re-reading a recording's audio is work the user should choose to spend.

Overlapping speech is presented as a floor rather than a total. The procedure was validated against AMI ground truth at precision 0.71 to 0.95 and recall 0.62 to 0.85 with a 250ms collar, stable on far-field audio and through 64kbps MP3, and it underestimates rather than overestimates. Interruption counts are gone and are not coming back on one mixed channel, which is the case argued in full in the evidence note.

Two notes carry over. If live capture and a transcription job contend for the same card, the 120 second GPU window may still be too large, and lowering it is a source change (GPU_MAX_CHUNK_DURATION_S in the ONNX ASR engine) rather than a setting. And the Codex payload in the worker-io image is a stripped static binary that vulnerability scanners cannot introspect, so that image passes the release scan because there is nothing to examine rather than because its contents were examined. Running the Claude path only, or skipping the io lane, avoids it.

### Browser-Capture Compatibility

<!-- Maintainer: note any change to supported browsers/OSes or capture behaviour. Default below. -->

Supported browsers, operating systems and audio sources are unchanged. Shared-audio capture still resolves on Chromium desktop and nowhere else.

What changed is what capture tells you. The shortfall warning is measured on the server rather than estimated in the browser, names an unreachable backend or a suspended tab only where there is evidence for it, and can be dismissed. The pre-flight notice on the Meet Now card names the Chrome Memory Saver setting to change before a meeting starts and is dismissed per browser, because the setting it asks for is per browser and a roaming preference would hide the notice exactly where it still applies. A screen wake lock is held for the duration of a capture and re-taken on return to the foreground.

An interrupted capture that banked no audio is now discarded rather than kept. A page unload during capture pauses the recording, so a Next.js reload just after Start Meeting used to raise a resume-or-discard decision over a meeting seconds old. A discard is announced by recording id, so the rail and the dashboard drop the row immediately instead of leaving it on screen until the next poll.

A documents panel and the capture toolbar introduced in 2.3.0 are unchanged. The columns around them are now sized against the window, which is covered above.

### Changes

{{CHANGELOG}}
