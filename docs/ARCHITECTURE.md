# Nojoin Architecture Overview

How Nojoin's services, processing pipeline and data fit together. Each section names the code that implements it.

## System at a Glance

Nojoin runs as one Docker Compose stack ([docker-compose.example.yml](../docker-compose.example.yml)). Running it is covered in [DEPLOYMENT.md](DEPLOYMENT.md).

| Service | Role |
| --- | --- |
| `nginx` | TLS proxy. Sends `/api/`, `/mcp` and `/.well-known/oauth-*` to the API and everything else to the frontend. |
| `frontend` | Next.js web client: capture, review, settings and administration. |
| `api` | FastAPI app (`backend/main.py`): REST API, the MCP server mounted at `/mcp` (`backend/mcp_server/`), auth, recording lifecycle and task dispatch. |
| `worker-gpu`, `worker-cpu`, `worker-io`, `worker-parse` | Celery workers, one per lane (below). |
| `db` | PostgreSQL with pgvector. |
| `redis` | Celery broker and result backend, plus short-lived guards and markers. |
| `socket-proxy` | Read-only Docker API, through which the API reads container logs for the admin log viewer. |

| Path | Contents |
| --- | --- |
| `backend/api/v1/endpoints/` | REST routes |
| `backend/worker/tasks/` | Celery tasks, including the `process_recording_task` orchestrator in `pipeline.py` |
| `backend/processing/` | ASR engines, VAD, diarisation, voiceprints, audio analytics, LLM backends |
| `backend/utils/canonical_pipeline/` | Canonical transcript writes, the projection, speaker corrections |
| `backend/services/` | Analytics derivation, exports, calendar sync, CLI OAuth |
| `backend/models/` | SQLModel tables |
| `frontend/src/` | Web client; capture is in `frontend/src/lib/capture/` |

Models: silero VAD; Whisper (default `turbo`), or Parakeet (`parakeet-tdt-0.6b-v3`) or Canary (`nemo-canary-1b-v2`) through onnx-asr; `pyannote/speaker-diarization-community-1` for diarisation; `pyannote/wespeaker-voxceleb-resnet34-LM` for voiceprints; `pyannote/segmentation-3.0` for overlap; `jinaai/jina-embeddings-v2-small-en` for search.

### Worker Lanes

Celery work is split into four queues so a long finalise never blocks light work. Routing is `TASK_ROUTES` in [backend/celery_app.py](../backend/celery_app.py), and an unrouted task falls to the GPU queue. Pool sizes are in [DEPLOYMENT.md](DEPLOYMENT.md).

| Lane | Work |
| --- | --- |
| `gpu` (one slot) | Final processing, live ASR, voiceprint extraction and rebuilds, overlap analytics, model downloads |
| `cpu` | Playback proxies, import audio extraction, delivery analytics, backups, the ffmpeg transcode of non-FLAC segments from older capture builds |
| `io` (also runs Celery Beat) | Meeting Edge, notes and meeting intelligence, speaker inference, chat, AI analysis, transcript indexing, calendar sync, restores, telemetry, cleanup |
| `parse` | Document parsing and RAG index rebuilds |

`worker-parse` runs the `worker-io` image, because visual parsing may go through a subscription CLI that ships only there. It is a separate lane because a document has no page cap and could otherwise hold an IO slot during a live meeting.

The GPU worker keeps the live ASR model loaded while any recording is `UPLOADING`, and otherwise releases model caches after each task. Every task reloads `config.json` first (`task_prerun` in `celery_app.py`), and a settings change applies from the next task without a restart. The API saves the file with a single rename, which keeps a reader from seeing a partial write.

### Dispatching From The API

`send_task` blocks on Redis, and the API dispatches from `async def` handlers. Request paths therefore dispatch through [backend/core/task_dispatch.py](../backend/core/task_dispatch.py), which publishes off the event loop: `dispatch_task`, or `dispatch_task_best_effort` where a failed dispatch must not fail the request. The API also gives up quickly: connects are capped at 2 s and `apply_api_dispatch_limits` cuts publish and result-backend retries to one, so an unreachable broker costs a dispatch about 6 s. Workers keep Celery's default retries and call `send_task` directly.

When editing `celery_app.py`, note that the broker reads its transport options while the result backend reads top-level `redis_*` keys, and that `send_task` subscribes the result backend before it publishes, so bounding the publish retry alone does nothing.

### AI Providers

LLM backends live in `backend/processing/llm_backends/` (Gemini, OpenAI, Anthropic, Ollama). Each user's AI calls resolve to the install-wide provider (an API key or Ollama) or **CLI OAuth**, which runs the user's own Claude subscription through the Claude Agent SDK, or ChatGPT subscription through the OpenAI Codex CLI, on the IO lane. CLI OAuth falls back through the server's provider chain on any failure, and no feature may depend on it; [SECURITY.md](SECURITY.md) covers the trust boundary.

An install-wide secondary provider (`secondary_llm_provider`, or `SECONDARY_LLM_PROVIDER`) wraps the primary in `SecondaryLLMBackend`. Any primary failure is retried on the secondary, which has its own models and key, and if both fail the primary's error is raised. A streamed answer falls back only when the primary failed before its first token.

### Document Parsing

`process_document_task` (parse lane, `backend/processing/documents/`) writes one `document_pages` row per page, slide, sheet or heading-bounded section, and records which tier produced each:

1. **Structural.** For PDFs, pypdfium2 text in the order the file draws it, which keeps columns apart, and pdfplumber tables found from their ruling lines; for Office files, the underlying XML, which yields slide titles, table cells, speaker notes and the values behind native charts.
2. **Visual analysis** (on by default) sends each page's images to the user's model. For PDFs and images the model sees the rendered page and its output replaces the text layer; for Office files it sees the embedded figures and its output supplements the structural text.
3. **Local OCR** (Tesseract, in the worker image) supplements pages with little text layer, and needs no AI provider.

Pages are written as they complete, so a restarted worker resumes at the first missing page. A provider that cannot take images raises `VisionUnsupportedError`, which drops the whole document to a structural parse with a warning; for an image upload, which has no text layer, it is an error.

Pages feed the search index (`context_chunks`, one chunk per page unless a page exceeds the embedding window), the notes prompt and the MCP `get_documents` tool. Document text is untrusted, and a vision model reproduces any instruction printed on a page, so the notes and chat prompts wrap it in `<attached_document>` tags with an instruction to treat it as data.

Each document's original file is stored flat in the documents directory under a name of its own (`<uuid>.<ext>`, or `<stem>__<uuid><ext>` when a restore finds the name taken), so no two rows share a file. The rules live in [backend/utils/document_storage.py](../backend/utils/document_storage.py):

- **Deletion.** Deleting a document removes its file, and so does every path that deletes a recording: delete, permanent and batch permanent delete, discard, deleting a user, and a restore in **Overwrite** mode (**Clear all existing data** empties the directory). Under **Overwrite**, a restored document that takes the name of the copy it replaced keeps that file: the restore unlinks before it moves files in, and skips any file a row still names, compared by device and inode so another spelling of it counts. The staged file of a restored row that fails to insert goes with the staging directory.
- **After the commit.** Files are unlinked only after the delete commits, so a failed commit never leaves a row without its file. A failed cleanup is logged and never turns the committed delete into an error. A cancelled unlink still runs, on the event loop, because it is the only thing that would remove the file.
- **Uploads racing a delete.** Deletes lock their recordings `FOR UPDATE` before reading the document paths. A document insert waits on the same lock, so an upload cannot commit a row the delete never read. The API's deletes take the locks in id order, and deleting a user locks the user row first. A restore in **Overwrite** mode does not: it locks the recordings it replaces before the user rows it inserts against, and locks one its pre-delete missed only on reaching it. A user deletion running alongside can then deadlock with it, and PostgreSQL aborts one of the two, which rolls back with no file removed. An upload writes its file before its row and removes it if the insert fails or is cancelled; an insert that lost the race answers 404.
- **Containment.** Only paths inside the documents directory are unlinked. A legacy row pointing elsewhere keeps its file, and the path is logged.
- **Parses.** A parse whose document is deleted stops at its next write. One that fails on a write is still marked `ERROR`, with a fixed message; the database's text goes only to the log.
- **Sweep.** A file left without a row, as by a process that dies between a commit and its unlink, is reported daily and removed only when configured ([Storage Hygiene](#storage-hygiene)).

### Meeting Analytics

Analytics come in four tiers. The evidence behind each design choice is in [ANALYTICS_EVIDENCE.md](ANALYTICS_EVIDENCE.md), and the read that assembles them is `read_recording_analytics` in `routes_analytics.py`.

| Tier | Source | Stored | Code |
| --- | --- | --- | --- |
| Speaking dynamics | `transcript_utterances`, derived on every read | No | `backend/services/meeting_analytics/` |
| Delivery | The audio, CPU lane | `analytics_payload.delivery` | `backend/processing/delivery_descriptors.py` |
| Overlap | The audio, GPU lane | `analytics_payload.audio_overlap` | `backend/processing/audio_overlap.py`, `backend/worker/tasks/analytics_overlap.py` |
| AI analysis | A model reading the transcript, IO lane | `analytics_payload.ai` | `backend/utils/meeting_analysis/`, `backend/worker/tasks/analytics_ai.py` |

The three stored tiers write one JSONB column from three lanes, so each merges its own top-level key under a row lock through `backend/utils/analytics_payload.py`.

Speaking dynamics (talk-time share, turn structure, response latency, a talk-share timeline, silence and overlap totals) are derived on read: every recording has them without a backfill, and a speaker merge, rename or transcript edit shows on the next read. Figures group by `recording_speaker_id`, which a merge redirects. The package has no ORM or ML imports below its query layer, and its thresholds are in `constants.py`. Rules every consumer relies on:

- Per-speaker talk time counts overlapping speech once for each speaker, so shares are reported against total speech, and both denominators are carried.
- Anything excluded from a statistic is counted.
- Finalised `UNKNOWN` lines, which the transcript view hides once any line has a speaker, are left out of every speaker figure, transition and warning, and counted under `metrics.unattributed`. Delivery and AI analysis read the same lines through `split_analysed_utterances`.
- There are no interruption counts. The merge gives each segment one speaker from a mixed stream, so a transcript holds no overlapping speech. Overlap is measured from the audio and never attributed to a person.
- Gaps at speaker changes are reported in three counted parts: immediate handovers (under the 250 ms collar, `LATENCY_COLLAR_MS`), a reply-time median over gaps from the collar to 5 s, and lapses of 5 s or more.
- A structured warning flags doubtful speaker attribution with reason codes (`low_share_clusters`, `speaker_cap_bound`, `unnamed_speakers`). The web client owns the wording and shows nothing for a code it does not know.

#### Delivery Descriptors

Speaking pace, pitch height and movement (YIN, spread in semitones), loudness and within-turn pausing. No emotion model is used, and none should be added. The module uses numpy and soundfile only and never imports torch. A file soundfile cannot open (WebM/Opus or MP4/AAC from captures made before FLAC capture; M4A, AAC, MP4 or WMA imports) is first decoded with ffmpeg to a temporary 16 kHz WAV that keeps its channels.

`compute_delivery_analytics_task` runs on the CPU lane as a post-processing follow-up, and from the Analytics tab's **Measure delivery** action for older recordings. In a browser capture, channel 0 is shared audio and channel 1 the microphone, so each utterance's dominant channel, judged with the live lane's thresholds, identifies its source; an imported stereo file is downmixed. Loudness is compared across speakers only when they shared a signal chain.

The payload carries `DELIVERY_METHOD_VERSION` and the transcript event watermark it was measured at. Stale figures are re-measured when the Analytics tab is next read, if the owner's `auto_remeasure_delivery` setting is on (the default), with a five-minute per-recording claim so the tab's polling cannot queue the run twice. A measurement that ended in error is never retried by a read. After a method-version bump, `remeasure_outdated_delivery_task` re-measures up to 25 recordings every six hours.

Cross-meeting baselines compare a speaker linked to a person with that person's medians over the user's other measured meetings. They are derived on read, use the same method version only, and need three such meetings (`BASELINE_MIN_MEETINGS`). The wording describes delivery, never emotional state.

#### Measured Overlap

`compute_overlap_analytics_task` runs `pyannote/segmentation-3.0` on the GPU lane. `backend/processing/segmentation_model.py` loads the model, and overlap is its only use. It is dispatched with the other follow-ups, and by **Measure delivery** for older recordings. Audio soundfile cannot open is decoded to a temporary 16 kHz mono WAV first. The result has its own method version and status under `audio_overlap` and needs no staleness check, since the audio does not change after processing.

It reports that people talked over each other, for at least how long, and where. It never says who overlapped whom, and the interface presents the total as a floor because the detector underestimates.

#### AI Analysis

A model reports the topics and who drove each, how each speaker's words read, which questions were asked and which went unanswered, and who proposed, agreed with or objected to each decision. The tier has its own `analytics_ai_status` column, which adds `unavailable` (no AI provider, a normal state) and keeps an AI failure from marking delivery as broken.

It runs on request, from the Analytics tab or the MCP `analyse_meeting` tool. An administrator may cap runs per user per UTC day (`ai_analysis_daily_limit` in `config.json`, overridable on the user record). Runs are logged in `ai_analysis_runs` when queued, a run past the cap gets 429, and the read reports the remaining budget. A user may opt in to running it after every processed meeting (`auto_analyse_meetings`, off by default), within the same budget.

The parser, not the prompt, enforces three rules:

- Speakers are an allowlist of the meeting's display names. Anything attributed to another name is discarded.
- A citation must appear in the transcript that was sent (normalised for case and punctuation, matched across turn boundaries) and its timestamp must fall inside the meeting. A sentiment or decision item left with no valid citation is dropped.
- Every discard is counted.

Sentiment is read from the words alone and never combined with delivery. The tier answers who drove topics and decisions; the decision log stays in the notes. Consensus is `stated`, `assumed` (the decision went unchallenged, which the interface must not show as agreement) or `none`. A very long meeting is truncated and the payload records how far the analysis reached. The payload carries a method version and transcript watermark, and when stale the tab offers to regenerate instead of calling the model again unasked.

### Exports

`GET /api/v1/transcripts/{id}/export` builds a PDF, DOCX or plain-text file from any of the notes, analytics and transcript (`backend/services/exports/`):

- `builder.py` turns a meeting into an `ExportDocument` of neutral blocks. Notes are parsed from Markdown with `markdown-it-py`. Analytics come from `read_recording_analytics`, the Analytics tab's own read, so an export cannot show different figures and never dispatches a measurement. The wording ports the tab's components (`formatDuration.ts`, `deliveryInsights.ts`, `AttributionWarning.tsx` and the AI panels), with tests that pin the parity.
- One renderer per format draws the blocks: ReportLab for PDF, python-docx for Word, and plain text. `charts.py` lays charts out once, painted as vectors in the PDF and as a PNG in Word. Rendering runs in a threadpool, off the event loop.
- Automatic speaker colours exist only in the browser, so the export dialog sends the colour map it shows; `speaker_colors.py` falls back to saved colours, then chart slots.
- The DOCX embeds Geist Regular and Bold as obfuscated font parts so Word does not substitute them. The fonts and their licence are in `backend/services/exports/assets/fonts/`.

### Web Client

The Next.js app in `frontend/` covers the dashboard, recordings and transcript review, analytics, speakers, notes, chat, documents, and user, admin and system settings. It is the only capture surface; a browser that cannot capture keeps everything else.

#### Connectivity Monitor

`frontend/src/lib/connectivity/` decides when the API is unreachable and is built to avoid false alarms. Any success (API traffic, a health poll or an idle probe) proves the backend is up, so a busy page never probes. Only the prober can declare an outage, once the last success is 30 s old and three probes in a row have failed. Probes are serialised, so a burst of failing requests cannot reach the threshold at once. A probe that overran its timeout by a wide margin is discarded, because a frozen tab suspends the timer and the request together, and returning to the foreground clears the failure streak. The monitor relies on elapsed wall-clock time because the `resume` event is not dispatched on every Chromium build. A false outage would also mislabel a capture shortfall as `backend-unreachable` (below).

### Browser Capture Stack

Capture lives in `frontend/src/lib/capture/`. Browser support and troubleshooting are in [CAPTURE.md](CAPTURE.md).

- **Sources.** On desktop the browser asks for shared tab, window or screen audio and the microphone and mixes them with Web Audio. Mobile Chrome records the microphone alone, leaving the shared channel silent.
- **Segments.** An AudioWorklet tap (`frontend/public/audio-worklets/pcm-tap.js`) feeds the two-channel mix to `frontend/src/lib/capture/pcm/`, which resamples it to 16 kHz with a windowed-sinc filter, cuts exactly 32,000 frames (2 s) per segment by counting samples, and encodes each as FLAC. Nothing restarts between segments, so they join with no gap. Channel 0 is shared audio and channel 1 the microphone.
- **Upload.** Segments upload one at a time, in order, with the session cookie. Each is written to IndexedDB when recorded and removed when the server acknowledges it, so a closed tab keeps what it had not sent, and the next resume or stop in that browser sends it first.
- **Unload.** A real tab unload (`pagehide` or `beforeunload`) drops the unfinished segment and pauses the recording with a beacon. In-app navigation and switching tabs do not. A paused recording must be resumed, stopped or discarded before another capture starts; stopping needs no live browser runtime.
- **Interrupted capture.** A beacon refused while signed out, or lost to an outage, leaves the recording `UPLOADING` with no tab capturing it. The recording payload's `capture_stalled` is true once a live capture (`client_status` `RECORDING`) has gone two minutes without an upload, start or resume (`capture_looks_stalled`, judged on the server's clock). The client lists the user's `PAUSED` and `UPLOADING` recordings and offers a stalled one in the resume-or-discard dialog, unless a tab of this browser holds its Web Lock (`frontend/src/lib/capture/captureLock.ts`), which the capturing tab takes in shared mode for as long as its capture runs and the browser drops when the tab goes. While the user has an unfinished capture and this tab runs none, the client looks again every 30 s. Resume accepts a stalled capture as if paused and refuses one still uploading, unless the request carries the current capture epoch, which only the tab holding the capture has ([Capture Epochs](#capture-epochs)). Nothing pauses or finalizes a capture automatically, since the server cannot tell a closed tab from one that is cut off.
- **Pause.** The recorder pauses and the tab reads paused at once. The controller then sends the queue and pauses the recording on the server, owing the pause as after an upload stall (below) when the request does not arrive. A pause the server refuses leaves the tab paused with the server's error, and is not sent again.
- **Stop.** Stop releases the microphone and shared tab as soon as the recorder has stopped, then sends the queue and finalises. Every stage is bounded and reports its stage, so a stalled recorder or uploader ends in a retryable finalise. The one unbounded wait is for the user to sign back in, below.
- **Upload stall.** A segment that fails six times in a row (about 15 s) stalls the uploader, which keeps its queue, and the controller pauses the recorder and then the recording. The tab reads paused and keeps its runtime and Web Lock whether or not that pause reaches the server. A failed stop, which leaves the server holding the capture as uploading, and a pause from the controls whose request does not arrive, owe the server a pause in the same way. An unacknowledged pause is sent again once the connectivity monitor records an answer from the API newer than the last failed attempt, after the queue drains, and before any resume, so it cannot land after one. The first retry goes on the first such answer. After two or more failures in a row the next waits 2 s, doubling to 30 s, since a pause that alone keeps failing while the API answers everything else would otherwise go again on every connectivity probe (issue #599). A resume sends it at once. It carries the tab's capture epoch, so once another tab or device has taken the capture over it is refused instead of pausing the capture there ([Capture Epochs](#capture-epochs)).
- **Session expiry.** While a capture runs, or a stop is still sending audio, the controller holds sign-in in place through `frontend/src/lib/session.ts`. A 401, or a 403 for a pending password change, then leaves the page where it is instead of reloading to `/login`, which would end the capture. Uploads and the pause, resume and finalise calls wait for the user to sign back in rather than counting as failures, and a dialog signs in through `POST /login/session`. The hold is pinned to the account that was signed in when it began: a sign-in as anyone else, including one made in another tab, which shares the cookie, ends the session for the capture instead of resuming it. Each request is stamped with the sign-in it was sent under, so a 401 that arrives after the user has signed back in changes nothing. Notes not saved yet hold sign-in the same way, with or without a capture ([Timeline Notes](#timeline-notes)). For the same reason, the recording page treats a 404 for its recording, from a poll or a reload of the view, as a removal only once `/users/me` names the account that first loaded the recording (`useRecordingNotFound.ts`), and leaves once a second request confirms the 404. Under another account's cookie it stays. Where sign-in is held, the answer goes to `noteSignedIn`, which opens the held dialog for the owner; otherwise the page says so once, and the check tells neither the session nor the notes store (`getUserMe`), so the tab's session, its notes persistence and any later hold stay the owner's. While the 404s go on, it asks again at most once a minute per tab. A page that never learnt its account claims no removal.
- **Wake lock.** A screen wake lock is held while capturing and re-taken on each return to the foreground. This prevents device sleep only; nothing in a page can stop the browser suspending a tab (see [CAPTURE.md](CAPTURE.md#chrome-memory-saver)).
- **Coverage.** A suspended tab stops recording without raising an error, so the client compares the server's `captured_audio_seconds` (on the recording detail payload, polled every 15 s) with elapsed recording time. The shortfall excludes audio still queued for upload, which is shown separately, and a queue over 30 s also shows on the recording controls. The warning names a cause: `backend-unreachable` when the monitor reports the API down, or did in the last five minutes (queued audio will upload on reconnect); `tab-suspended` when the page was seen to thaw or the recorder watchdog caught a stall (the audio is lost; this outranks an outage); otherwise `unknown`. Once dismissed, it returns only if the shortfall grows materially.

## Recording Flow

A recording moves through `UPLOADING` (capturing) and `PAUSED`, then `QUEUED`, `PROCESSING`, and `PROCESSED` or `ERROR`.

1. **Start Meeting** calls `POST /api/v1/recordings/init`, which creates an `UPLOADING` recording owned by the user and returns its capture epoch ([Capture Epochs](#capture-epochs)). Init, segment, pause, resume, discard and finalise all use the browser session.
2. The browser uploads each segment to `/recordings/{id}/segment?sequence=N&epoch=E`, numbering from 0.
3. The API decodes each FLAC segment to a 16 kHz, two-channel WAV, registers it (see [Chunk Registration](#chunk-registration)) and dispatches the live lane. A segment that is not 16 kHz, two-channel, 16-bit FLAC, or will not decode, is quarantined as a `.corrupt` tombstone and the upload still succeeds, costing a 2 s gap. WebM, Ogg and MP4 segments from tabs on an older build go to the CPU lane for an ffmpeg transcode first.
4. `POST /recordings/{id}/finalize` accepts an `UPLOADING` or `PAUSED` recording, so a capture whose browser is gone can still be processed. Missing sequences and pending transcodes get a retryable 409. Otherwise it rebuilds chunk offsets and window manifests from the segments on disk, seals the cursor, joins the segment WAVs into a FLAC master with soundfile, takes the duration from the frames written, and queues `process_recording_task` and `generate_proxy_task`. The browser sends its elapsed recording time, which the `capture_coverage` pipeline metric compares with the stored audio.

A recording can be discarded while uploading, paused, queued or processing. Discard revokes the running task with `terminate=True` and removes the recording's row, so a requeued task finds nothing to process. Once that delete has committed it removes the recording's files: its audio, its segments and its documents. Finished recordings go through the normal delete.

### Chunk Registration

Each segment becomes a `recording_audio_chunks` row with an offset on the recording timeline, and each 20 s window (5 s hop) a `recording_audio_window_manifests` row on which the live lane tracks ASR progress. `backend/utils/recording_audio_registration.py` registers a segment in constant time:

- A per-recording cursor (`recording_audio_chunk_cursors`) records how far chunks are placed. A chunk is placed once every earlier sequence is placed or has failed for good (a `.transcode_failed` marker or a `.corrupt` tombstone); a segment that arrives early waits until the gap fills.
- Offsets are cumulative over the chunks present, and a missing sequence ends a contiguous group of windows. Placing chunks can only grow the last group, so registration rebuilds windows from the first one that was not yet full, writes only new or changed ones, and leaves the status of the rest alone. The result matches a full rebuild.
- Registration is serialised per recording with `pg_advisory_xact_lock`, so it never locks the `recordings` row. The upload writes `last_activity_at` at most every 15 s.
- Finalise runs the full rebuild as reconciliation and then seals the cursor. A sealed cursor makes registration a no-op, so a transcode task still queued at stop cannot reopen sealed windows. A resume after a failed stop drops the seal and rebuilds the cursor from the chunk rows, as happens for a recording that was in flight across an upgrade.

### Capture Epochs

`recordings.capture_epoch` names the tab holding a live capture, so a tab whose capture was resumed elsewhere cannot overwrite the segments recorded since (issue #570). Segments are numbered by the browser and stored as `<sequence>.flac`, so without it two tabs writing the same sequence replace each other's file and chunk row.

- **Issue.** Init starts the epoch at 0. A resume without an `epoch` takes the capture over and issues the next one. A resume with the current epoch is the holding tab carrying on: it leaves the epoch as it is and is accepted whether the server holds the capture paused or still uploading, since no other tab or device can be running it.
- **Fence.** Segment, pause, finalize and discard requests carry the sender's epoch, and one from an earlier epoch is refused with a 409 whose detail code is `capture_superseded`, as is the old tab's own resume. A request without an epoch is not checked: tabs on an older build send none, and a stop or discard from a dialog or the recordings list acts for no tab.
- **Ordering.** A segment streams to an `.upload-tmp` name and is given its sequence number only after the epoch check, under the recording's audio advisory lock (`_lock_capture` in `backend/api/v1/endpoints/recordings/capture_audio.py`). Resume issues an epoch under the same lock, and finalize holds it from its first read to its commit. A segment from the old epoch therefore lands before a takeover reads the last sequence, and the new tab numbers after it, or it is refused; and no resume can take a capture over while its master is built.
- **The old tab.** A refused request makes the tab stop its recorder, release its media, Web Lock and uploader, and read idle with the message that the recording continued elsewhere. Its unsent segments are kept to download (Left-out audio, below).
- **A capture discarded elsewhere.** Discard deletes the row, so the old tab's requests answer 404. So does a request sent under another account's sign-in, which a sign-in in another tab causes by replacing the shared cookie. The controller therefore stands down on a 404 only once `/users/me` names the account the capture belongs to (`ownerId`, taken when it starts) and the recording still answers 404 to a second request (`captureGoneError` in `frontend/src/lib/capture/controller.ts`). Under another account, a tab holding sign-in passes the answer to `noteSignedIn`, which opens its dialog, and the request waits for the owner (issue #602). A discard that finds the recording already gone succeeds.
- **A capture stopped elsewhere.** A stop from a dialog finalizes without an epoch and issues none, so the tab that held the capture still holds the current epoch. Its requests are refused instead by the recording's status, with a 409 whose detail is `Recording is no longer accepting capture uploads`, and the tab stands down in the same way on the first one, saying the recording was already stopped. Discard accepts a queued or processing recording, so a discard that carries an epoch is refused with that 409 too once the recording is neither `UPLOADING` nor `PAUSED` (issue #590).
- **Kept audio.** Each stored segment records the epoch it is queued under, and a runtime that adopts kept segments stores them again under its own. A takeover in the same browser sends them only when they were kept under the epoch just before it, and a stop with no live capture sends only those kept under the epoch the server holds, under that epoch. Anything else was recorded before someone else carried the capture on, would land out of order, and leaves the queue to be kept to download, as does any audio past a gap in the queue.
- **Finished elsewhere.** Kept audio is sent only when this browser resumes or stops its capture, so audio kept for a capture stopped, discarded or taken over and finished on another device would otherwise stay. Each time the client lists the user's unfinished captures it looks up every other recording the store holds audio for. It keeps that audio to download when the server reports the recording finished, and removes it when the server answers 404 while `/users/me` names the account recorded with the audio (`ownerId` on each stored segment), since the recording was discarded. Another account gets the same 404 for a recording it does not own, so a 404 for audio with no owner or another owner keeps it, and the page does not ask about it again (issue #589).
- **Left-out audio.** Audio that can never join its recording, as above, is joined in the browser into one FLAC file (`joinFlacSegments` in `frontend/src/lib/capture/pcm/flacJoin.ts`) and kept in an IndexedDB database of its own, `nojoin-left-out-audio`, with the id of the account it was recorded under (issue #605). Each segment is a FLAC stream that numbers its frames from 0 and ends on a short block, so the joiner rewrites every frame header as a variable-blocksize frame numbered by sample, recomputes both CRCs and copies the subframes unchanged; a backend test reads a joined fixture with libFLAC. `frontend/src/lib/capture/leftOutAudio.ts` offers each file in a persistent notification to the signed-in account that owns it, again on every page load until it is downloaded or deleted, and removes any more than seven days old, whoever owns it. The audio stays out of `nojoin-capture` because a new store there needs a version upgrade, which any tab still open on an older build would block.

## Processing Pipeline

`process_recording_task` ([backend/worker/tasks/pipeline.py](../backend/worker/tasks/pipeline.py)) runs on the GPU lane:

1. **Resolve audio.** Validate the source, restoring it from the proxy if needed.
2. **VAD** ([backend/worker/tasks/vad_stage.py](../backend/worker/tasks/vad_stage.py)). Resample to 16 kHz mono and mute non-speech with silero VAD (`enable_vad`). Under 1 s of speech completes the recording with an empty transcript. Each later stage then reads either the muted file or the unmuted one (see [Muted Audio Per Stage](#muted-audio-per-stage)).
3. **Transcription** through an engine in [backend/processing/engines/](../backend/processing/engines/) (see [ASR Engines](#asr-engines)), less the words it placed in digital silence (see [Words In Digital Silence](#words-in-digital-silence)).
4. **Diarisation** with pyannote (`enable_diarization`), on the unmuted file unless `vad_mute_diarization` is set, bounded by the recording's `max_speakers` when set. Phantom speaker filtering then takes clusters within `phantom_max_duration_s` and `phantom_max_segments`: one below `phantom_embedding_floor` against every main speaker is treated as noise and reassigned, one above `phantom_merge_threshold` merges into the speaker it matches, and one in between is kept.
5. **Merge and consolidation** (see [Word Timestamps In The Merge](#word-timestamps-in-the-merge)).
6. **Speaker assignment.** Voiceprint extraction, identification against the People library, and the duplicate-merge pass (see [Speakers And Voiceprints](#speakers-and-voiceprints)).
7. **Canonical finalise**, when `enable_canonical_transcript_writes` is on (the default). `finalize_utterances_from_segments` in [backend/utils/canonical_pipeline/core.py](../backend/utils/canonical_pipeline/core.py) writes the merged segments as finalised utterances, supersedes every other active utterance (the live lane's included) while keeping the user's locked text and speakers on lines it matches, and rebuilds the transcript projection. This is committed before step 8, so a later failure cannot undo it. With the setting off, the segments stay in the transcript's JSON only.
8. **Meeting intelligence**, when a provider and model are configured: title, Markdown notes, and speaker names applied to unresolved speakers and kept on the transcript as an audit trail. Local Ollama runs inline; any other provider is handed to the IO lane (`generate_meeting_intelligence_task`). Without AI the recording still completes, and **Generate Notes** and **Retry Speaker Inference** work once AI is configured.
9. **Completion.** Auto-link to a calendar event, then queue the follow-ups in [backend/worker/tasks/followups.py](../backend/worker/tasks/followups.py): transcript indexing (IO), delivery (CPU), overlap (GPU) and, if the owner opted in, AI analysis (IO).

The playback proxy is built by `generate_proxy_task` on the CPU lane, queued alongside processing. `POST /recordings/{id}/reprocess` deletes the generated transcript, utterances, speakers, chat and index rows (keeping the user's notes) and requeues the pipeline, optionally with a different engine and model (`engine_override`).

### ASR Engines

The engine and its model are install-wide settings.

- **Whisper** re-decodes a window by sampling when its greedy output fails a quality check. The sampling is seeded, so reprocessing on the same image gives the same transcript.
- **Parakeet and Canary** share `OnnxAsrEngine`. They transcribe in windows of 15 to 30 s that close at a pause (either may be shorter when less than 30.2 s is left), and skip a window with no signal (`_chunk_boundaries` in `onnx_asr_engine.py`).
- onnx-asr returns no token timings for **Canary**, so each Canary window is one segment and takes its dominant speaker in the merge.
- For **Parakeet** it returns each token's start only. `map_onnx_asr_recognition` ends each word at the earliest of the next word's start, the window's end, and its start plus `asr_word_length_cap_s` (default 0.4 s), never before the word starts. A word stretched across a pause could otherwise end inside the next speaker's turn and be given to them. The cap cannot exceed flip smoothing's default maximum word length (0.45 s), so a capped word stays short enough to smooth.
- **Parakeet** decodes once more any stretch of a decoded window (input of 30 s or less is one window) that holds 3 s or more of kept audio and no word, in calls of at most 20 s (`onnx_asr_backstop.py`). A stretch ends at the pause before the next word or 0.16 s before its stamp. It reads exact zeros as VAD silence, so it runs only when `VAD_MUTED_INPUT` says the audio the engine reads was muted: with VAD off, with `vad_mute_transcription` off, in the live lane and with Canary nothing changes.
- A stretch that retry leaves wordless is decoded a second time from the last subtoken of the word before it, keeping only the words that start inside the stretch. A stretch with no word before it in its window gets no second try.
- Invariant: a stretch is retried at most twice, and nothing is retried a third time. Stretches don't overlap, and neither do second tries, so the retries decode at most twice the window's own audio. One INFO line per recording counts both retries, what the second recovered or skipped, and what is still wordless.

Each user's transcription language reaches live, final, import and reprocess ASR and is part of the ASR result hash. Whisper gets a language only when one is selected, Canary gets its source-language parameter, and Parakeet always auto-detects.

### Words In Digital Silence

Whisper can emit a word or two, most often "Thank you.", in the silence the VAD stage muted to exact zeros. After transcription, `drop_unheard_words` ([backend/worker/tasks/final_segments.py](../backend/worker/tasks/final_segments.py)) reads the exact-zero runs of the audio the engine read and drops each word lying inside one run with `UNHEARD_WORD_GUARD_S` (0.5 s) of zeros to spare on each side ([backend/processing/unheard_words.py](../backend/processing/unheard_words.py)). A segment that loses every word goes too, a segment without word times is judged on its own span, and the result's text is rebuilt. A word that straddles the edge of kept audio, or sits nearer it than the guard, is kept, since word times stray a little from the sound they mark.

- It reads the zeros rather than the VAD's speech regions, so it only touches audio the engine heard as silence: the gaps between regions, the regions under the 250 ms minimum that the mute step zeroes, and the muted first and last second. On unmuted input, with VAD off or `vad_mute_transcription` off, the only runs are true digital silence, so a word outside the VAD's regions, often speech it missed, is kept.
- Every engine is treated alike. Canary's windows have no word times and skip audio without signal, so they are seldom touched.
- It is best effort: audio that cannot be read keeps every word. The `final_asr_unheard_words` pipeline metric counts what went.
- The live lane does not run it. Finalise transcribes the recording again, so no live line reaches the final transcript.

### Muted Audio Per Stage

With `enable_vad` on, the VAD stage keeps two files: the normalised 16 kHz mono file, and a copy with every sample outside speech, and the first and last second, set to exact zero. Two per-user settings choose which one each stage reads (`muted_stages` in [backend/processing/vad_muting.py](../backend/processing/vad_muting.py), issue #486). `vad_mute_transcription` defaults to true, the muted file, and `vad_mute_diarization` to false, the unmuted one. The worker's merge applies no user defaults, so an unset, null or non-bool value takes the default in `VAD_MUTE_DEFAULTS`, which the user defaults in `config_manager` also read.

- `vad_mute_transcription` covers the engine and the zero-run drop above. `VAD_MUTED_INPUT` follows it, not `enable_vad`, so the Parakeet backstop runs only on muted input.
- `vad_mute_diarization` covers diarisation, the phantom filter and final-pass voiceprint extraction, which score the clusters diarisation found. Voiceprint rebuilds always read the source recording.
- Invariant: muting zeroes samples and never cuts them, so both files have the same rate, length and time base. Word times and turn times need no offset whichever file each stage read.
- VAD runs whenever `enable_vad` is on, so the no-speech short-circuit holds with both switches off. With `enable_vad` off, both stages read the unmuted file, whatever the switches hold.
- A word that unmuted transcription places more than `GAP_WORD_TURN_TOLERANCE_S` from any turn of a muted diarisation stays `UNKNOWN`: hidden in the transcript view, left out of Meeting Chat's transcript and counted as unattributed, but kept in exports, meeting notes and `transcript.text`.

### Word Timestamps In The Merge

`combine_transcription_diarization` in [backend/utils/transcript_utils.py](../backend/utils/transcript_utils.py) treats each run of consecutive segments by what it has. A run with word timings is aligned word by word; a run without (Canary, or a Whisper segment with an empty `words` list) gets each segment's dominant speaker. Every segment's text reaches the output. A word no longer than pyannote's segment precision takes the speaker whose turn contains its instant (`[start, end)`; a turn's end counts when no turn starts there). A word that overlaps no turn takes the speaker of the nearest turn within `GAP_WORD_TURN_TOLERANCE_S` (0.5 s), the earlier turn on a tie, unless the attributed words either side share a speaker with a turn that near, whose speaker it takes. Neighbours are read as diarised, before any gap word is given a speaker. A word further than that from every turn stays `UNKNOWN`. Single-word flip smoothing then gives a word of at most `word_flip_max_duration_s`, within `word_flip_max_gap_s` of neighbours that share a speaker, to that speaker.

Consolidation never drops text. A segment under 0.1 s folds into a neighbour at most 1.0 s away if the result stays within 10 s, preferring the same speaker and then the nearer one; the receiver keeps its speakers. A fragment with no qualifying neighbour stays as a short segment, and only a fragment without text is dropped. Neighbours whose fragments all folded away merge only if the original pieces tiled (gaps under 0.01 s) with the same speakers, within 10 s. A segment over 10 s is split in an 8 to 12 s window.

Invariant: float rounding decides no threshold on a word time, or on a gap or duration built from word times. Parakeet times sit on onnx-asr's 80 ms grid (or the cap past it) and Whisper's on hundredths of a second, so a gap can equal a threshold exactly. Every such comparison, in the mapper, the merge, flip smoothing, consolidation, the fold and the live lane's context trim, goes through [backend/utils/word_times.py](../backend/utils/word_times.py), which treats a value within 1 µs of its threshold as equal to it. Speaker-overlap ratios are not word-time thresholds and compare as they are.

### Processing Tuning

Eight values in these stages can be overridden. Keys, defaults and bounds live in [backend/processing/processing_tuning.py](../backend/processing/processing_tuning.py), which imports nothing heavy so the API validates against the same table, and the processing modules take their defaults from it. [frontend/src/lib/processingTuning.json](../frontend/src/lib/processingTuning.json) mirrors it.

| Key | Stage |
| --- | --- |
| `vad_threshold` | VAD, final and live |
| `asr_word_length_cap_s` | Parakeet word ends, final and live |
| `phantom_max_duration_s`, `phantom_max_segments`, `phantom_embedding_floor`, `phantom_merge_threshold` | Phantom filter |
| `word_flip_max_duration_s`, `word_flip_max_gap_s` | Flip smoothing |

Defaults and ranges are in [DEPLOYMENT.md](DEPLOYMENT.md#processing-tuning-defaults).

Either phantom ceiling at 0 turns the phantom filter off, and a `word_flip_max_duration_s` of 0 turns smoothing off.

Each value is a per-user setting. A stage resolves it as an explicit argument, else the recording owner's merged settings (their value over the install's flat `config.json` key), else for VAD the install's `vad_parameters.threshold`, else the shipped default. User defaults stay `None` (inherit), because the settings page autosaves the whole object and a concrete default would be written onto every row.

A tuning value never fails a recording. The API rejects out-of-range values, but a stored value can still be unusable (hand-edited, restored from a backup, or saved before a bound tightened), so the worker logs it and uses the shipped default (for VAD, `vad_parameters.threshold`). The phantom floor must be below the merge threshold: the API checks the pair the worker will read, and the worker treats a conflicting pair as both defaults. GET returns the install's flat values as a read-only `processing_tuning_install` map so the settings page can run that check. New code must read these values through `resolve_tuning`, or `get_vad_config_from_settings` for VAD.

### Transcription Failure Versus Silence

Invariant: an empty `completed` transcript means the meeting was silent, never that ASR failed.

An engine's `transcribe()` returns the canonical result or raises `TranscriptionError` ([backend/processing/engines/errors.py](../backend/processing/engines/errors.py)), never `None`; empty text with no segments is a success. The dispatcher `transcribe_audio` wraps anything else an engine raises, so `TranscriptionError` is the one exception callers treat as a lost transcription, and code that calls an engine must not turn it into an empty result. The message is written for the UI. `gpu_out_of_memory` marks a GPU out-of-memory failure, which counts only when the model ran on CUDA (an onnx-asr model on CPU reports the server out of memory). After an out-of-memory error the engines drop every reference to the model, including those held by the traceback's frames, so the memory is freed. Celery time limits and termination are re-raised unchanged.

Final processing retries a GPU out-of-memory failure once, after releasing every cached model and the CUDA cache. A failure that remains sets `transcript_status` to `error` with the message in `error_message`, marks the recording `ERROR`, and stops before diarisation and everything after it. Provisional live text is kept. Only reprocessing repairs it; **Retry Speaker Inference** returns it to `ERROR`.

`error_message` is shared with notes failures, and `transcript_status` decides whose it is. Only the `Transcript` methods `fail_transcription`, `complete_transcription` and `set_notes_error_message` write it, which keeps a notes run from clearing a transcription failure; a backup restore that settles an interrupted recording uses the same path. Notes cannot be generated for a failed transcription: the API answers 409 and a notes task already queued does nothing. A later run that completes clears the failure, and if it finds no speech it leaves `notes_status` alone.

### Speakers And Voiceprints

`Recording.max_speakers` is an optional upper bound, settable at import, on reprocess and during a live capture. `NULL` (the default) passes no speaker keyword to pyannote. A value is passed as `max_speakers`, never `num_speakers`, because an exact count forces a split whenever the user overcounts.

A person (`global_speakers`) belongs to one user (`user_id`). Identification, the voiceprint dialog, renames and every other lookup read only the people of the recording's owner, so a recording speaker links only to a person in its owner's library.

Voiceprints are comparable only within one extraction procedure. `EMBEDDING_METHOD_VERSION` in [backend/processing/embedding_version.py](../backend/processing/embedding_version.py), which imports no torch, is stored on `global_speakers.embedding_version` and `recording_speakers.embedding_version`. Identification and the duplicate-merge pass refuse to score across versions. Any change to how embeddings are produced must bump the version, and any new comparison must check `embeddings_are_comparable`. Thresholds are in [backend/processing/embedding.py](../backend/processing/embedding.py).

Every write must keep a stored voiceprint's version true:

- A freshly extracted vector is stamped `EMBEDDING_METHOD_VERSION`.
- A copied voiceprint (restoring a backup) keeps its own version.
- A blend goes through `blend_voiceprint`, which never mixes versions. A current voiceprint replaces a stale one without the drift guard, since a cosine across versions says nothing; a stale one leaves a current row unchanged; an empty row takes the incoming vector and its version.

The duplicate-merge pass (`backend/processing/speaker_merge.py`) joins clusters the diariser split from one voice:

- The threshold is `DUPLICATE_SPEAKER_MERGE_THRESHOLD` (0.70). Lowering it causes wrong merges, which are harder to undo than wrong splits.
- Linkage is complete: two groups join only when every pair across them clears the threshold, so no third cluster can bridge two people.
- A group carrying a person's identity is held to that person's library voiceprint. If any cluster in the proposed union, other than those already identified as that person, scores below `IDENTIFIED_MERGE_VOICEPRINT_FLOOR` (0.60, deliberately not tied to `DRIFT_GUARD_THRESHOLD`), the union is refused and that cluster keeps its `Speaker N` name. A library voiceprint from another extraction version is not consulted.
- Each finalised utterance keeps its diariser cluster in `transcript_utterances.diarization_label`, so `unmerge_recording_speaker` can restore a merged cluster as an unnamed speaker with all its lines except those a person assigned by hand, recording an `unmerge_speakers` correction event. Lines written before that column existed carry no label; undoing those merges needs a reprocess.
- Overlap labels are left as diarised in the `Transcript.segments` projection, and `serialize_canonical_utterances` resolves them on read: a merged speaker's label reads as the speaker it was merged into, and a label that resolves to the line's own speaker is dropped. A merge made in the speaker panel therefore leaves no raw label in an overlap lane, and an unmerge brings the label back. Processing's own merges rewrite overlap labels before finalise, so undoing one of those leaves them on the survivor.

Every link of a recording speaker to a person offers its voiceprint through `update_library_voiceprint` in [backend/processing/embedding.py](../backend/processing/embedding.py):

- **Speech floor.** Only a recording voiceprint with at least `LIBRARY_VOICEPRINT_MIN_SPEECH_S` (20 s) of speech founds a library voiceprint; a shorter one links without it. Every extraction records the figure on `recording_speakers.embedding_speech_seconds`. An unmeasured voiceprint founds as before, and nothing re-extracts just to measure. `RecordingVoiceprint.from_task_result` refuses an extraction result with no figure, so a result from an older worker is never grandfathered as unmeasured.
- **Figures follow extractions.** Speech is never pooled across recordings. A merge within one recording adds the figures when its fold passes the guard, and an unmerge takes them out.
- **Drift guard.** A fold is refused when `cosine(current, incoming) < DRIFT_GUARD_THRESHOLD` (0.70).
- **Refusals stay on the link.** A link keeps every person the guard refused it for, one row each in `recording_speaker_drift_refusals`, keyed by the link and the person. A `skipped_drift` link gains a row for that person, and a later save or fold of the link into them deletes only their row (`record_drift_verdict`). A link a lock keeps out (`skipped_locked`) is judged against the locked voiceprint instead (`judge_drift`); any other outcome is no verdict. While the speaker is linked to a person it has a row for (`is_drift_refused`), every rebuild or pooling of that person's voiceprint from links leaves it out. A save, fold or refusal for someone else leaves the row alone, so a link that returns to that person without a new verdict is still left out.
- **A new voiceprint is judged.** A segment moved onto the link, a speaker merged into it, the voiceprint dialog's extract and the extract-all endpoint, and a processing run that finds the speaker already there (named during capture, or a run requeued after an interrupted one) each give the link a voiceprint offered to no one. The guard compares it with the voiceprint of the link's person, which stays as it is (`judge_drift`): a refusal adds or keeps that person's row, a pass deletes it, and rows for anyone else stay. With no person, or no voiceprint of the same version, nothing changes.
- **Other changes.** A split's remainder deletes all of the link's rows (`forget_drift_verdict`), since the split decides whether it joins the original person. Re-extracting the same speech after a method change keeps them. Reprocessing deletes the recording's speakers, and their rows with them. Merging two people moves the source's rows to the target, keeping one where a link was refused for both. Deleting the link or the person deletes its rows (`ON DELETE CASCADE`).
- **Locks.** `is_voiceprint_locked` blocks the pipeline's fold, auto-applied name suggestions and segment learning. Every user action that would change a person's voiceprint shares one rule (`lock_blocks_user_link`): a rename on either write path, **Add to People** into an existing person, the voiceprint dialog's link, and merging another person into them. The lock blocks any change to an existing voiceprint, a stale one included (`skipped_locked`), but a locked person with none still gets one. The user's explicit overrides are the dialog's force link (`force_link`), which folds through the guard and leaves the lock set; recalibrating and a split, which set it; and deleting the voiceprint, which clears it. The person update (`is_voiceprint_locked`) sets or clears it alone, leaving the voiceprint as it is. A lock never stops identification.
- **Versions.** A current voiceprint replacing a stale one is a founding and needs the floor.
- **Pooling.** Recalibration and a person split extract one voiceprint per selected recording (`pool_recording_voiceprints`) and average only those that meet the floor. Recalibration alone overrides the guard: in each recording it uses, it lifts the refusal of the person's links with a line its samples cover more than half of.
- **Splits.** The original person is rebuilt from their remaining clips, else from their unrefused links in other recordings (`saved_from_other_recordings`), and cleared only if neither qualifies, since the old voiceprint holds the moved clips. A cleared row keeps the current `embedding_version`; a NULL one reads as legacy and the rebuild task would found it again. An original with one speaker in a split recording gives that speaker the remainder's voiceprint, so a rebuild can't average the moved clips back in. The receiving person is locked when the split saves or folds their voiceprint; the original keeps their lock unless their voiceprint is cleared.

`rebuild_voiceprints_task` repairs stale voiceprints from Celery Beat every six hours, at most 25 recordings a tick (`AUTOMATIC_VOICEPRINT_REBUILD_LIMIT`). A stale voiceprint that cannot be re-extracted (its audio is gone, or its speaker has no transcript segment) is cleared; a transient failure leaves it for the next run. It averages only unrefused links that meet the floor, without re-running the guard, since a cosine against an old-version voiceprint means nothing, and skips a person with a locked current-version voiceprint. A stale person with no qualifying current-version speaker keeps the stale voiceprint, which matches nothing; only one with no current-version speaker at all is cleared.

A speaker correction (a rename across the recording or from an utterance forward, a merge, a library link or promotion) changes only the utterances its scope selects, or the speaker's identity. It never re-derives other utterances from `diarization_window_results` or `diarization_window_turns`, which hold data only for live captures finalised by v1.0.0 to v1.2.0.

Three `pipeline_metric` stages make diarisation quality readable from a worker log:

- `final_diarization_speaker_stats`: per-cluster speech duration, segment count and share, overlapped speech, and whether the cap bound.
- `speaker_identification`: each cluster's three closest people with cosine scores, and which matched.
- `speaker_merge_pass`, emitted on every run, including runs that merge or score nothing (with a reason): pair scores, why a pair over the threshold was refused, each merged group with its survivor and scores, and each absorbed cluster's score against the person's library voiceprint.

### AI Calls

- **Language.** The notes language is separate from the transcription language. Notes, meeting intelligence, title generation and secondary fallback all get the same output-language instruction. Prompt control text and JSON keys stay fixed, and the automatic intelligence contract accepts any non-empty top-level Markdown heading.
- **User notes** are anchored on the audio timeline and reach every notes, Meeting Edge, chat and speaker prompt; see [Timeline Notes](#timeline-notes).
- **Transcript reads.** Notes, Meeting Edge and Meeting Chat read segments through `build_transcript_segments_for_read` (the canonical utterances, else the `Transcript.segments` projection) and name speakers with `build_recording_speaker_map`. The canonical read runs in a savepoint, so a failure leaves the caller's transaction usable. Chat renders through `render_transcript_for_llm` (`backend/utils/llm_transcript.py`) as `[MM:SS]` lines, without the `UNKNOWN` lines the transcript view hides, with the user's notes inline; notes generation still sends them. The pipeline's first-pass intelligence and the MCP `get_transcript` tool format their own lines.
- **Meeting Chat** sends the notes and full transcript every turn, plus excerpts from the search index (`_chat_retrieval_filters` in `routes_chat.py`): documents attached to this meeting or to meetings its tags bring in, and transcript excerpts from those other meetings only. The index is rebuilt only after processing, so its excerpts can predate an edit. Chat answers 409 while a recording is in `IN_FLIGHT_TRANSCRIPT_STATUSES` (paused, uploading, queued or processing).
- **Prompt caching.** Chat sends the notes and transcript as the system prompt (Anthropic marks it with `cache_control`; OpenAI-compatible providers reuse it through prefix caching), leaving history and the question in the messages. Meeting Edge and AI analysis split their prompts into a stable instruction prefix, which Anthropic marks, and the per-call context.

#### Ollama Context Window

Left at its defaults, Ollama cuts an oversized prompt from the front (current releases keep about half the window) and shifts the context when the answer fills it, both ending in an ordinary stop. Hosted providers reject an oversized request. `OllamaLLMBackend._post_chat` therefore makes Ollama refuse instead:

1. `num_ctx` is `ollama_context_window` (the operator's VRAM budget, default 32768), lowered to the model's trained context from `/api/show` when that is smaller and never raised. `/api/show` is cached for five minutes per server and model.
2. Every `/api/chat` request carries `truncate: false`, which makes Ollama answer an oversized prompt with HTTP 400 before generating (0.34 and later include the token count), and `shift: false`, which ends an answer that fills the window with `done_reason: length`. No `num_predict` is sent, leaving a Modelfile's own limit in place. The 400 becomes a context-window error and a length stop an incomplete-answer error; neither answer is used.
3. Meeting chat drops the oldest turns itself, estimating 2.6 UTF-8 bytes per token and leaving a quarter of the window (at most 2,048 tokens) for the answer. The last message, which carries the meeting context and the question, is never cut. If Ollama still refuses, chat retries once with fewer turns, then reports the refusal.
4. Servers before 0.12.6 ignore both flags, so their token counts are checked after the call, and a prompt plus answer past `num_ctx` is refused as truncated.

A refusal raises `ContextWindowExceededError` (`backend/utils/context_window.py`), which the Ollama backend re-raises unwrapped. A secondary provider takes over as for any primary failure, including a streamed chat, since the refusal arrives before the first token. A length stop at the end of a streamed answer, or a truncation found after the call on an old server, withdraws the answer with no fallback. Speaker suggestions and document pages report the refusal as their error. Notes and chat continue as below.

#### Meetings Longer Than The Context Window

For long meetings, use a hosted provider, or Ollama with a hosted secondary that receives the whole meeting after Ollama refuses it. Only when there is no secondary, or it also fails, does Ollama write the notes in parts. `SecondaryLLMBackend` re-raises the primary's refusal, so the worker sees the same `ContextWindowExceededError` either way and calls `generate_meeting_notes_in_parts` (Regenerate Notes) or `generate_meeting_intelligence_in_parts` (the automatic pipeline), which the wrapper sends to the primary. The steps are in `backend/utils/long_meeting_notes.py`, with the splitter in `backend/utils/transcript_parts.py`:

1. **Budget.** Ollama 0.34 and later report the refused prompt's token count, which gives this meeting's tokens per byte, plus a tenth for parts denser than the whole. Without a count, parts are priced at 2.2 bytes per token.
2. **Split.** The transcript is split between lines (utterances and anchored notes, a note kept with its line where it fits) into the fewest roughly equal parts that fit, never fewer than two. An utterance longer than a part is cut between sentences, each piece keeping its timestamp and speaker. A part's room is the window less a quarter for the answer (at most 8,192 tokens) and less the rest of the prompt. If that leaves under a quarter of the window, the run is refused with a message naming the attached documents, the part of the prompt that grows without limit.
3. **Draft.** Each part is drafted with the same prompt prefix (instructions, speaker labels, metadata, glossary, user notes, calendar context, attached documents and the notes structure), followed by the part's number, times and transcript, so a server that keeps its prompt cache reads the prefix once. A draft Ollama refuses, or whose answer runs out of window, is halved and both halves drafted.
4. **Combine.** Combining calls keep a third of the window (at most 16,384 tokens) for the answer, leave out the attached documents, and are priced as prose at 2.5 bytes per token. Word targets let about three drafts share a call, and drafts too many for one call are combined in groups, then the groups. A refused combining call is halved, and two drafts Ollama will not combine end the run with an error. Each call is retried once after an Ollama server error.
5. **Speakers and title** (automatic pipeline). Calls in parts answer in Markdown with tagged lines, because a small model held to `format: json` over dozens of calls often loops or abandons JSON. Each draft lists the unresolved speakers its part gives evidence for in a `<speaker_names>` block. A speaker who introduced themselves is named by `detect_rule_based_speaker_suggestions`, as in a run without AI. A name every part agreed on is kept. For a label the parts disagree on, the final call picks one of their suggestions or none, and only an exact match counts, since names are applied without review. The final call also writes a `<title>` line, and names go into the notes afterwards with `apply_speaker_mapping_to_notes`.

A meeting that fits keeps the single call and a byte-identical prompt.

When every provider has refused the whole transcript before any text streamed, Meeting Chat asks once more with excerpts. `_transcript_excerpts` in `routes_chat.py` finds this meeting's most relevant transcript chunks by the question's embedding, and `render_transcript_excerpts_for_llm` reads the lines at those times from the canonical transcript, which picks up edits made since indexing. Lines are taken most relevant first up to the room left and shown in time order with `[...]` at each gap. On Ollama that room is the window less the answer room, the notes, the question, and up to an eighth of the window for recent turns. The prompt says the excerpts are partial. With no query embedding or no index for the meeting, chat answers from the notes alone and says so.

#### Timeline Notes

The user's notes are `timeline_notes` rows (`backend/models/timeline_note.py`) at `/transcripts/{recording_id}/timeline-notes`, for the recording's owner only (anyone else gets 404).

- **Owned by the recording**, not the transcript, so a reprocess keeps them. Notes and tombstones go with the recording (`ON DELETE CASCADE`).
- **Kinds.** `point` (`anchor_ms`), `selection` (`quoted_text` over `start_ms`..`end_ms`), and untimed `head` and `tail`, each shape fixed by a check constraint and the API schemas. The notes box edits the earliest head note; MCP `append_meeting_notes` adds a tail note.
- **Times** are milliseconds of audio without paused time, the clock both transcripts share. During capture the detail endpoint reports `live_audio_ms` (`backend/utils/live_audio_clock.py`): the end of the last segment, run on from its arrival or from the last resume, whichever is later. The page runs it on between polls only while capture advances (`frontend/src/lib/notes/audioClock.ts`), so every device anchors alike. While capture advances the page's clock never runs backwards; a pause sets it to the paused figure, which is exact.
- **Reading them.** The detail read carries `timeline_notes_list_signature` (`count:hash` of each note's id and revision), not the notes, and the page fetches the list when it changes (`frontend/src/lib/notes/useTimelineNotes.ts`), and after its own writes when nothing polls. Every write that changes a listed note makes a new revision, so the signature changes whenever the list does.
- **Placement.** One rule, implemented twice: `place_timeline_notes` (`backend/utils/timeline_notes.py`) for prompts, and `placeTimelineNotes` (`frontend/src/lib/notes/placement.ts`) for the live and finished transcripts, both pinned by `backend/tests/fixtures/timeline_note_placement_cases.json`. A note at t (a selection at its last millisecond) follows the last non-blank line, in list order, starting at or before t. A note before the first line opens the transcript.
- **Views** place notes on their whole line list. A note after a hidden line shows after the visible line before it, and a note ends the run of grouped lines it follows. The views sort lines by start, while the prompts keep the stored order, which every writer emits by start: lines tied on start may sort either way without moving a note past a non-blank line (a property test in `placement.test.ts`). A list stored out of start order, which `PUT /transcripts/{id}/segments` accepts, would place a note differently in the views than in the prompts.
- **Prompts.** An anchored note is its own line after the line it follows, folded so it cannot pass for transcript. Head and tail notes go in `# User Notes Context`, quoted with `> `; chat puts them first and last. Speaker inference reads every note as a timestamped list. Generated notes end with `## User Notes`, which speaker renames never rewrite.
- **Meeting Edge** refreshes at once, ahead of its throttle, when `timeline_notes_signature` changes. It hashes each note's id and content (kind, text, anchors, quote, author, creation time), not its revision as the list signature does, so an edit that changes none of these refreshes nothing.
- **Revisions.** An edit names its base revision, and a delete may (`?expected_revision=`). A stale one is a 409 `revision_conflict` with the current note.
- **Write tokens.** A write may carry a client-chosen `write_token`, and the note reports the one that made its revision (`last_write_token`), so a client recognises its own write. An edit with a token always makes a new revision, fencing off an earlier write still in transit.
- **Tombstones.** A delete leaves a `timeline_note_tombstones` row with its token, so later writes to that id get a 410 (`note_deleted_by_you` or `note_deleted_elsewhere`) and a retried create never revives the note. A delete with a token of an unknown id leaves a `never_held` tombstone (kept an hour, at most 20 per recording), so a create whose answer was lost cannot outlive the user's clear. Client-id creates and deletes take `pg_advisory_xact_lock(hashtext(id))`. Backups omit tombstones, and a restored note whose id is tombstoned gets a fresh id.
- **Errors** carry `detail: {code, message}`. Clients branch on `code`, pinned by `backend/tests/fixtures/timeline_note_error_codes.json`. The notes box's sync states are tabulated in `frontend/src/lib/notes/headNoteSync.ts`, and the live note box's in `frontend/src/lib/notes/noteOutbox.ts`. The live box's outbox is one per recording for the life of the page, so notes on their way and the box's text outlive the live view. A note on a line from its ⋯ menu, in the live or the processed transcript, goes through the same outbox as a `selection` note quoting that line. So does a note on selected text, whose `start_ms`/`end_ms` are estimated: word times don't reach the client, so each word's time is interpolated by character offset across its line's span (`frontend/src/lib/notes/selectionRange.ts`), and the span covers every word the selection touches. Its `quoted_text` is the text as selected.
- **Unsaved notes in the browser.** Each outbox is also kept in IndexedDB (`nojoin-notes`), one record per account id and recording holding each unsaved note with its id and write token, the box's text and any discard whose delete is unconfirmed (`frontend/src/lib/notes/notePersistence.ts`). The id comes from `/users/me` and survives a rename. Once the session names the account, opening the recording again reads its record back and sends each note under the same id and token, so a create that landed before a reload returns its note rather than a second one, and a 409 or 410 is handled as on the first attempt; after a 409 the note goes out under its new id only once the record has it, or once a write under way has gone unanswered for 2 s (`NEW_ID_WAIT_MS`): a tab that closes or crashes before that write answers can leave the old id stored (a close no longer asks first, as the note has gone out), and since the server refuses that id again whatever the token, a reload makes a second copy of the note (it takes an id clash with another recording, a stalled store and the tab closing or crashing, together). A page writes only while its session is the account it read as, no sign-out has stopped storing, and no cleanup has removed that account's records since. Each `/users/me` drops every other account's records. The tab tells the others over a BroadcastChannel (`nojoin-notes`), as it does a sign-out, which stops every tab storing until a sign-in is accepted in it; a tab told of either repeats the removal for its own account, after any write it had begun, so a write that landed between the removal and the ping doesn't outlive it; without BroadcastChannel, other tabs go by their own sessions. Two tabs on one recording share the record, and the last to write wins. The record is therefore only a stopgap for a crash, a killed tab or an ended session: the leave warning (`noteOutboxes.ts`) is armed whenever anything is unsent, whatever is stored (browsers that never show it, such as iOS Safari, have only the stored copy), and the sign-in dialog's way to the sign-in page goes through it. Records carry a `version`: an earlier version's record is upgraded one step at a time through `RECORD_MIGRATIONS` and left alone where a step is missing, a later version's is neither read nor written, and a note of a kind this version cannot send is written back unchanged. A new kind is therefore not a new version: `selection` notes add `startMs`, `endMs` and `quotedText` to their own stored notes only, and an older build keeps them as they are. A browser without IndexedDB, or one that refuses, keeps the outbox in memory only. While any outbox holds something unsaved, the page holds sign-in in place as a capture does (Session expiry, under [Browser Capture Stack](#browser-capture-stack)), so a device that only takes notes asks for the password instead of reloading to `/login`.
- **Unsent marks.** The recordings rail marks each recording with something unsaved in this browser (`useUnsentRecordings` in `frontend/src/lib/notes/useNoteOutbox.ts`). A recording this page holds an outbox for goes by that outbox; any other by whether the signed-in account has a record for it (`NoteStore.recordingsOf`), read when the page loads and again whenever this page's outboxes change, so another tab's writes show at the next read. Nothing is sent from the rail: opening the recording restores and sends as above, because a sender in the background would act on notes another tab may have open. Deleting a recording for good or discarding it here (`forgetRecordingNotes` in `noteOutboxes.ts`, called by the recording actions, the batch bar and a recording page that finds its recording gone for its owner) drops the page's outbox for it once any write of it under way has finished, removes the signed-in account's record, and tells the other tabs (`forgot` on the `nojoin-notes` channel), which drop their outbox for it and remove the record again. A record for a recording deleted on another device is never marked. Once per page load and account, when the rail's list first loads, `useForgetNotesOfDeletedRecordings` (`frontend/src/lib/notes/deletedRecordingNotes.ts`) checks the account's records whose recording is neither listed nor held by an outbox here, at most 20, two at a time, with `GET /recordings/{id}` (issue #636). A 404 `Recording not found` between two `/users/me` answers naming the record's account forgets it through `forgetRecordingNotes`, since under another account's session cookie the owner's recordings answer 404 too. Any other answer keeps it, a binned recording's 200 included; `backend/tests/test_recording_detail_after_delete.py` pins those answers.

### Imported Media Input

Import stores no video. Every import route (`/import`, chunked finalize, `/upload`) stores the upload as it arrived, marks the recording `QUEUED` with the step "Extracting audio..." and queues `keep_imported_audio_task` on the CPU lane ([backend/worker/tasks/imported_audio.py](../backend/worker/tasks/imported_audio.py)) in place of processing. The task runs `keep_imported_audio` ([backend/utils/import_audio.py](../backend/utils/import_audio.py)), points `audio_path` at the result, rebuilds the import's audio window, deletes the upload once that write has committed (a failed deletion is logged), then queues processing and the playback proxy.

- **Extraction.** For any media container, or any upload carrying video, the audio track ffmpeg selects by default is extracted into the recording's `temp/<id>/`. WAV, MP3, AAC and FLAC uploads are kept as they arrived without probing, so they need no ffmpeg.
- **Format.** AAC, ALAC, MP3, Opus, Vorbis and FLAC are stream-copied, PCM and other lossless codecs become FLAC, and anything else becomes Opus.
- **Verification.** The result is kept only if it is one audio stream no more than max(1 s, 0.1%) shorter than the source track, measured from packets.
- **Concurrency.** Each write is conditional on the recording still waiting with the same `audio_path`, and the upload survives until the kept audio is stored, so a duplicate or re-queued task is harmless. The extracted file moves beside the upload only after its write has matched the waiting row. Tasks are acknowledged on receipt, so the CPU worker re-queues every waiting import when it starts (`requeue_imports_waiting_for_audio`). Delete, permanent delete, batch permanent delete and discard read the row `FOR UPDATE`, so they never delete a path the task has just replaced, and unlink the paths they read only once the delete has committed, so a failed commit keeps every file its row names. An ffmpeg left running by a killed worker writes only into `temp/<id>/`, which deleting the recording removes and the daily sweep clears.
- **Failures.** A bad file (no audio track, or one ffmpeg cannot extract) and a server fault (a missing tool, a timeout, a signal, a full disk) both mark the recording `ERROR` with the reason as its step and remove its files, upload included. Neither is retried, and reprocess refuses a failed recording with no audio, giving that reason.
- **Unfinished imports.** A chunked import sits in `UPLOADING`, with no client status, while its 10 MB parts arrive, and each part moves `last_activity_at`. Its page can close or lose its connection first, and then it stays there. The capture lock (`_get_active_capture_recording_for_user`) and the worker's check for a capture in flight, which keeps the live ASR model resident, count only live captures (`is_live_capture` and `live_capture_clause` in `backend/models/recording.py`: a client status of `RECORDING` or `PAUSED`, or the `PAUSED` status), so an import never blocks **Start Meeting** or holds the model (issue #569). The recording payload's `import_stalled` is true once no part has arrived for 15 minutes (`import_looks_stalled`), and the client offers such an import to import again or discard, after looking it up again (`UnfinishedImportNotice.tsx`). An import whose part fails is discarded by the page that sent it. One whose finalize fails is kept, because the finalize may have queued it before its answer was lost.

### Live Transcription Lane

While a recording is `UPLOADING`, `transcribe_segment_live_task` ([backend/processing/live_transcribe.py](../backend/processing/live_transcribe.py)) produces provisional text with the install's transcription engine.

- **Sequence gate.** Tasks for consecutive segments can run out of order. Each reads `next_expected` from the recording's `live/state.json`. A task ahead of it returns at once, leaving its WAV on disk, and the task holding `next_expected` drains the contiguous run present. Audio from an unfinished utterance is carried over in `live/buffer.wav` and joined to the next run, so an utterance split across segments is normally transcribed whole. Continuous speech is force-emitted after `live_forced_max_s` (8 s).
- **Regions.** VAD regions are padded (`live_speech_pad_ms`) and prefixed with a rolling context clip (`live/context.wav`, `live_context_window_s`) so word edges are not clipped; the output is sliced back to the region.
- **Writes.** The lane writes provisional canonical utterances, then refreshes `Transcript.segments` as a projection. The lane writes each utterance with speaker `UNKNOWN`; live speaker labels, when enabled, are written separately (below). The lane does record the capture channel: per-channel RMS decides whether the microphone or shared audio dominated, stored in `confidence_payload` and exposed on utterance reads as `source_channel` (`microphone`, `system`, or null when ambiguous). It identifies a channel only: a microphone-only capture reads as microphone for everyone in the room. Live utterance ids are a uuid5 of recording id, span, speaker and text.
- **Reads.** The live panel polls `GET /transcripts/{id}/utterances` every 3 s. The recording detail payload withholds in-flight transcript text and segments, so provisional text reaches no other surface.
- **Failures.** The lane is best-effort: a failed run is logged and the lane still advances. A failed run still accounts for the audio it drained. If it had carried audio into `live/buffer.wav`, the buffer is kept and `buffer_abs_start` moves to its start; otherwise `buffer_abs_start` moves past the audio the run read and the buffer and context clip are removed. Advancing `next_expected` alone would replay the stale buffer and stamp every later utterance early. The new state is saved in one write before any audio is removed.
- **Speaker labels.** With `live_speaker_labels` set to `auto` in `config.json` and the GPU worker on CUDA, `run_live_speaker_labels` ([backend/processing/live_diarization/step.py](../backend/processing/live_diarization/step.py)) runs after the run's text and lane state are saved, in a try block of its own, so a failure there is logged and never fails the run, changes its text or delays `next_expected`. It slides a 5 s window in 0.5 s steps over the run's new audio with the bundled segmentation-3.0 and WeSpeaker embedding models, from the worker's own caches, and assigns each step's local speakers to global speakers with online clustering vendored from diart (MIT, notice in [clustering.py](../backend/processing/live_diarization/clustering.py)). A voice too short to found a speaker is held, never forced onto an existing one, and a global re-cluster every 10 s can relabel earlier turns until a turn's label survives two passes or is 10 minutes old, when it locks. A re-cluster takes every unlocked turn and every turn of the last 20 minutes, at most 1,000, plus 10 older turns of each label, its latest long turns first (only long turns found clusters), which can no longer change but let a speaker heard long ago keep their label; its cost stays flat however long the meeting runs. A line takes the label that holds a strict majority of its diarised speech (held speech counts against it), and is shown provisional until it is complete and every piece of it is locked. The labels are written in a second transaction, only to `PROVISIONAL` live utterances without a manual speaker: the transaction holds the recording row `FOR NO KEY UPDATE`, so finalize's status change waits for it while audio chunks and utterance events, which only reference the row, do not. It locks the transcript, then reads those lines `FOR UPDATE SKIP LOCKED`: a line a person is editing waits for the next run and is counted open, so the lines after it are not settled past it. The settled cursor assumes lines commit in the order they end, which the live lane does; a segment PUT that moves an open line's end to or before the cursor leaves that line with its unlocked label until finalize. A speaker or text edit locks its line before checking the client's revision, so it sees a label written meanwhile, and a correction to every line of a speaker first locks every active line in id order, its own among them, so two such corrections take turns. The run then rebuilds the projection's speaker fields from every live provisional row, settled or not, since an edit that read the projection before the labels committed writes that older copy back. `speaker_label` becomes `LIVE_PROVISIONAL_<n>` (a namespace apart from the older `LIVE_<nn>` labels, which the pipeline makes speakers of), the lock goes in `confidence_payload` under `live_speaker_label`, and each change appends a `live_speaker_label` utterance event and bumps the revision, so the `after_revision` read returns the line. The cursor is the highest event id read, so a label that commits after an edit with a later id falls behind it; the live panel also reads the whole transcript every 10th read. It runs one read at a time, skipping a tick that finds one running and abandoning a read unanswered after 15 s, so a slow read never puts an older transcript back and a lost one never stops the feed. No `RecordingSpeaker` row is created, and none is made from one later: an edit that rebuilds lines from the projection (find and replace, undo, a restore) keeps the label on the line. Every reader shows `LIVE_PROVISIONAL_<n>` as "Speaker <n>", the live panel's name for it (`backend/utils/live_speaker_labels.py`): the LLM prompts (Meeting Edge, chat, notes), exports, the MCP transcript tools, search indexing and analytics, which also leave it out of the unnamed-speakers warning. The JSON APIs keep the raw label, and the views map it. The engine's state lives in `live/speaker_labels.npz`, rewritten by each run, and `live/speaker_labels.bank`, appended with 1 KB per turn. A save syncs the bank, then the state before its rename, then the directory, so the state on disk never counts a turn the bank lacks (about 0.5 MB and 60 ms a save after 2.5 hours, 40 ms of it the two added syncs, on a RAID array); a state that cannot be read, or one from another version or configuration, stops labels for that recording (`live/speaker_labels.off`), its text unaffected. A capture that runs across the upgrade to state version 2 (only installs that ran `main` while it wrote version 1; no release did) stops its labels at its first run after the upgrade, and its lines keep the `LIVE_<n>` labels written before it. A run that finds the capture already ended does no model pass and removes any state left. With two GPU lanes the next run's step can start before this one ends, so each step holds an `flock` on `live/speaker_labels.lock`, waiting up to 10 s for the previous one before it skips its run's labels. Off, the default, the live task imports none of the label code, so no model or file is touched.
- **Window manifests.** Each manifest has separate ASR and diarisation fields. The live lane writes ASR status (rows from older releases can also read `catch_up_processed`). Nothing writes diarisation status any more, so new windows stay `pending`. The legacy `status` field is a compatibility projection; new logic should read the lane fields.

At finalise, `process_recording_task` transcribes the whole recording again and writes over the live utterances, keeping the user's locked text and speakers on lines it matches. Live text is not reused. Live speaker labels are not reused either: a finalise that succeeds reads no live label, and its transcript is the same with labels on or off. If the final pass fails, the live lines stay as the recording's transcript until a retry replaces them, labels included: they still show as "Speaker <n>", and editing them makes no `RecordingSpeaker` of a live label. The live rows a final pass supersedes keep their labels in the database, as they keep their text; no reader returns them. Finalising the capture deletes the label state, as does a finalise whose master cannot be built (before the segments move to `failed/`), and a label step still running then writes nothing and removes what it saved. Deleting or discarding the recording removes its whole temp directory, and storage hygiene never lists files under `temp/`.

### Meeting Edge

After new live segments land, `refresh_meeting_edge_task` is dispatched best-effort: a failed dispatch is logged and the triggering request still succeeds. The task sends the provider a bounded recent transcript window, the previous run's rolling summary (a model-maintained 150 to 300 word running context), the previous suggestions so still-relevant ones are kept, the user's notes and focus text, and linked calendar context, and asks for strict JSON. Gemini (`response_mime_type`), OpenAI (`response_format`, retried without it for incompatible endpoints) and Ollama (`format: json`) use native JSON modes; Anthropic relies on the prompt. A tolerant parser accepts fenced or inline JSON from any of them. Changing the context-level slider or the enable toggle also refreshes the user's in-flight recordings, and the context level is part of the refresh signature.

Meeting Edge uses the configured provider's live model when one is set, else its main model.

A Redis single-flight guard (`backend/core/single_flight.py`) admits one refresh per recording at a time. Surplus triggers are dropped, since the next carries fresher transcript, and recorded as `meeting_edge_refresh_skipped`. The guard is a key with a TTL, so a worker killed mid-refresh releases it on expiry, and it fails open when Redis is unreachable.

### Utterance Public Ids

`transcript_utterances.public_id` is unique across all recordings. A segment's `id` is only a request, because openai-whisper numbers its segments in the same field. The merge keeps only string ids, so those numbers reach finalise only through the fallback that skips the merge, and backfill only through older projections. Finalise and backfill (`replace_utterances_from_segments`) take ids from `UtterancePublicIds` in [backend/utils/canonical_pipeline/public_ids.py](../backend/utils/canonical_pipeline/public_ids.py): an id no utterance holds is kept (finalise also requires a canonical UUID); one this recording already holds is replaced by a fresh uuid4 and recorded in the new row's `confidence_payload.source_public_ids`; anything else gets a uuid4. The bulk segment edit's full-replace fallback keeps the client's ids and answers 409 when one is held or repeated.

### Startup Canonical Cutover

`backend/entrypoint.sh` runs Alembic (`backend.startup_migrations`) and then `backend.startup_canonical_cutover` before the API starts. The cutover takes a database advisory lock and classifies every recording whose `pipeline_generation` is unset:

- `legacy_backfilled`: canonicalised, and viewable through the compatibility projection.
- `legacy_reprocess_required`: in flight during the upgrade, or not safely convertible. It must be reprocessed before transcript or speaker edits.

Recordings created or rebuilt by the current pipeline are `unified`, the only state with full edit support. Operator switches for the cutover are in [DEPLOYMENT.md](DEPLOYMENT.md).

## Stall Detection

A stall during live capture loses audio: the browser keeps counting time while nothing reaches the server. The API runs a watchdog on its event loop (`backend/utils/stall_watchdog.py`) that reports two things:

- **Event-loop lag**, the overshoot on a fixed sleep, as an `event_loop_stalled` pipeline metric when it exceeds the threshold. A blocked event loop, a throttled cgroup and a host paging the process out all show here.
- **Pressure Stall Information**, read from `/proc/pressure` when lag is detected, names the cause, since PSI's `avg10` window still reflects a stall that has just ended. It is absent on non-Linux hosts and kernels without PSI, where lag is reported without a cause. Sustained high pressure without a stall is reported as `host_pressure_high`, rate-limited.

Nothing is logged on a schedule. Settings are in [DEPLOYMENT.md](DEPLOYMENT.md#stall-detection).

## Anonymous Telemetry

Two Celery Beat tasks on the IO lane report to the collector under a random install id held in `data/.install_id`, with no meeting content, names or addresses. What is sent and how to turn it off are in [TELEMETRY.md](TELEMETRY.md).

- `send_telemetry_heartbeat_task` checks every 15 minutes and sends the install id and version when the install has never sent one, has started on a new version, or has not sent one this UTC day.
- `send_telemetry_ping_task` sends the usage diagnostics every six hours once the install consents. The collector upserts on install id and UTC day.

Each value has one writer: the API writes the `telemetry_*` keys in `config.json`, the worker writes the Redis send markers, and the install id file is write-once. Sending is best-effort: the diagnostics never retry and a failed heartbeat waits an hour. The collector, a Cloudflare Worker with a D1 database, is in [telemetry/](../telemetry/).

## Calendar Flow

Setup and sync behaviour are in [CALENDAR.md](CALENDAR.md).

- Synced events, with descriptions and attendees, are stored in `calendar_events` under `calendar_connections` and `calendar_sources`. The dashboard combines them with recordings not linked to an event.
- `sync_calendar_connections_task` runs on the IO lane every 15 minutes, incremental through each provider's change cursor (Google `syncToken`, Microsoft Graph `delta`). With live sync on and a public HTTPS origin, Google `events.watch` channels and Graph subscriptions (`calendar_push_channels`) trigger an immediate incremental sync by webhook.
- A sync that cannot be incremental (the monthly shift of the 25-month window, a missing or reset cursor, or the Microsoft partial-occurrence repair) fetches the whole window and reconciles it by `provider_event_id`, updating events in place. Events the listing omits are deleted, except those that ended before the window and are linked to a recording.
- `recordings.calendar_event_id` is nullable and `ON DELETE SET NULL`. Processing links a recording to a confidently overlapping event, or the user links one by hand. A linked event feeds the notes and speaker prompts and replaces the recording's own item on the dashboard calendar.

## Authentication Model

Token handling is in [backend/api/deps.py](../backend/api/deps.py) and [backend/core/security.py](../backend/core/security.py). Containment, revocation and policy are in [SECURITY.md](SECURITY.md).

| Client | Credential |
| --- | --- |
| Browser | Secure HttpOnly session cookie (token type `session`). Cookie-authenticated unsafe requests must come from the trusted web origin, checked by `Origin`, else `Referer`. |
| API clients | Bearer token (type `api`). |
| MCP connectors | OAuth 2.1 bearer token (type `mcp`) from Nojoin's own authorisation server, accepted only at `/mcp`. Read tools need `mcp:read` and write tools `mcp:write`; see [MCP.md](MCP.md). |

All three carry the user's `token_version` and are checked against the `revoked_jwts` denylist. Browser capture uses the normal session, and the retired native-helper routes answer `410 Gone` pointing to [CAPTURE.md](CAPTURE.md). A user flagged for a forced password change can reach only their own profile, the password update and logout until they change it.

## Storage and Persistence

- **PostgreSQL** with pgvector holds metadata, transcripts, speakers, embeddings and settings. Migrations are Alembic, in `backend/alembic/`.
- **Redis** is the Celery broker and result backend and holds short-lived guards and send markers.
- **`./data`**, mounted at `/app/data`, holds `config.json` (install-wide settings), `recordings/`, `documents/`, logs and the install id.
- **Secrets.** Calendar OAuth client secrets and tokens, push-channel secrets and CLI OAuth credentials are encrypted with `DATA_ENCRYPTION_KEY` (`backend/core/encryption.py`).
- **Backups** are built by `create_backup_task` on the CPU lane and restored by `restore_backup_task` on the IO lane (`backend/core/backup/`); see [BACKUP_RESTORE.md](BACKUP_RESTORE.md).

### Storage Hygiene

`backend/utils/storage_hygiene.py` finds the document, audio, proxy and partial-upload files that no row names, with the listing and the reference read in `storage_hygiene_scan.py` and the circuit breaker in `storage_hygiene_breaker.py`. The daily `cleanup_temp_recordings` runs it last, and `python -m backend.storage_hygiene` runs it on demand ([DEPLOYMENT.md](DEPLOYMENT.md#storage-hygiene)).

- **Flag by default.** The daily run removes nothing unless `NOJOIN_STORAGE_HYGIENE` is `delete`, and the CLI nothing without `--delete`. Rows whose file is missing are reported and never changed. An exception in a run is logged and never fails the daily cleanup.
- **Listing, then rows.** A run lists the documents directory and both recordings roots, `recordings_root_dir()` and `PathManager().recordings_directory`, resolved and each once, before it reads `audio_path`, `proxy_path` and `documents.file_path` in one statement, soft-deleted rows and documents without a recording included. A file written after the listing is never a candidate. The rows are read again before the first unlink, so a row committed before then keeps its file.
- **Keys.** A listed file is referenced when any of its keys matches a row's: its device and inode, as `document_file_identity` gives them; its file name, compared without case, so a stored path that resolves elsewhere still keeps the file; and, for a recording that is `UPLOADING` or `PAUSED`, its name up to the first dot, which keeps the `.flac` master and its `.part` that finalize writes beside the `.wav` the row names. Keys only ever keep more files.
- **Names.** Only names Nojoin writes are candidates: `<uuid4>[.restored][.ext]`, `<name>__<uuid4>[.ext]`, `demo_welcome_<id>[.ext]`, and the `.part`, `.<32 hex>.upload-tmp` or `.<8 hex>.tmp` of one of those. Any other file is reported as unrecognised and never removed. An unreferenced `.mp3` is a proxy when a file of its base name sits beside it, and audio otherwise.
- **Grace.** Nothing modified in the last 24 hours is removed, which covers a file whose row has not committed yet. Each file's inode and mtime are checked again right before its unlink.
- **Scope.** Only regular files directly inside each directory. `temp/` and `failed/` belong to the existing 24-hour sweep, and dotfiles and symlinks are left alone.
- **Circuit breaker.** Any rule makes a run report only:
  - an empty `recordings` table while the directories hold files;
  - a directory that is the data directory, contains it, or holds `config.json`;
  - more than half of one column's stored paths naming missing files, leaving out recordings that are pending, in error or cancelled, which can lack their file by design;
  - more than half of the stored paths relative to the working directory naming missing files;
  - more than half of a directory's recognised files past the grace unreferenced, which no option lifts, logged at ERROR when the run would remove files and at WARNING otherwise;
  - removing more than 10% of a directory's files or bytes, which `--allow-bulk <directory> --expect <count>`, given once per directory, lifts for that directory when the count is the dry run's;
  - the restore lock held or unreadable. It is read before the listing, before the first unlink and again every 25 flagged files, and fails closed, because a restore proceeds without it when Redis is down. Its 6-hour expiry bounds what it covers;
  - in a deleting run that every other rule lets through, a directory with candidates whose clock trails the worker's by more than an hour, logged at ERROR. The grace compares mtimes with the worker's clock, but an NFS server stamps mtimes itself (nfs(5), "File timestamp maintenance") and the Linux cifs client may write its own back on close. The restore lock is read again, then the run creates `.nojoin-hygiene-clock` with `O_EXCL` in each directory it would remove files from, writes and fsyncs it so it is stamped as Nojoin's own files are, reads its mtime back from the filesystem and removes it in a `finally`. The lag is measured from the later of the grace's start and the current time, so a worker clock stepped back mid-run cannot hide a bad cutoff. On `ENOSPC` or `EDQUOT` the empty probe's creation stamp stands in, which can only show as much lag or more. A probe that cannot be created trips the rule, which fails closed, and so does one already there: a run never writes or removes a file it did not create, and a probe it could not remove stops the next run rather than piling up. The name is a dotfile, so no listing, concurrent ones included, sees it. A directory with no candidates is not probed, since a lagging clock only makes files look older; one that had candidates and is gone trips. A report-only run, or one another rule stops (a restore included), writes nothing, and a clock running ahead only keeps more files, so it needs no rule.
- **Audit.** Every removed file is logged at INFO with its size, mtime and category, and a trip at WARNING with its rules, or at ERROR where noted above. A run that removes nothing lists its candidates, the daily run up to 50.

### Data Model

Tables are SQLModel classes in [backend/models/](../backend/models/). The canonical utterances are the source of truth for a transcript; `Transcript.segments` is a projection rebuilt from them.

| Area | Tables |
| --- | --- |
| Recordings | `recordings` (status, `audio_path`, `proxy_path`, `max_speakers`, `pipeline_generation`, `capture_epoch`, `calendar_event_id`), `transcripts` (one per recording: the segment projection, notes, statuses, `analytics_payload`), `timeline_notes` and `timeline_note_tombstones` (the user's notes) |
| Canonical transcript | `transcript_utterances` (state, revision, manual locks, `diarization_label`, `recording_speaker_id`), `transcript_utterance_events`, `processing_runs`, `speaker_correction_events`, `recording_speaker_aliases` |
| Speakers | `recording_speakers` (per recording, optionally linked to a person), `global_speakers` (the People library and its voiceprints), `recording_speaker_drift_refusals` (the people the drift guard refused each speaker for), `p_tags` and `people_tags` |
| Live capture | `recording_audio_chunks`, `recording_audio_chunk_cursors`, `recording_audio_window_manifests`; `diarization_window_results` and `diarization_window_turns` hold legacy data only |
| Documents and search | `documents`, `document_pages`, `context_chunks` (text embeddings for search and chat), `chat_messages` |
| Organisation | `tags`, `recording_tags`, `user_tasks`, `user_task_tags`, `user_task_recordings`, `notes_templates` |
| Users and access | `users`, `invitations`, `revoked_jwts`, `oauth_clients`, `oauth_authorization_codes`, `oauth_refresh_tokens`, `cli_oauth_credentials`, `cli_usage_daily`, `ai_analysis_runs`, `async_task_ownerships` (which user may poll a Celery task) |
| Calendar | `calendar_provider_configs`, `calendar_connections`, `calendar_sources`, `calendar_events`, `calendar_push_channels` |

## Release Model

A `vX.Y.Z` tag releases the API, worker, worker-io and frontend images to GHCR together, and the app reads release metadata from GitHub Releases. [scripts/release.py](../scripts/release.py) builds, scans and smoke-tests the images on the maintainer's release host and pushes them before the git tag. [release.yml](../.github/workflows/release.yml) then verifies and signs each image and only after that moves the rolling `major.minor` and `latest` tags, so a failed release never reaches `latest`. Anyone editing CI, the release workflow, the script or the Dockerfiles must keep that ordering, the SHA and digest pinning, and the signing identity. The full process is in [DEVELOPMENT.md](DEVELOPMENT.md#release-workflow-and-version-detection), and image verification for operators in [DEPLOYMENT.md](DEPLOYMENT.md#verifying-an-image-before-deploying).

## Related Docs

- [CAPTURE.md](CAPTURE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [DEVELOPMENT.md](DEVELOPMENT.md)
- [SECURITY.md](SECURITY.md)
- [ANALYTICS_EVIDENCE.md](ANALYTICS_EVIDENCE.md)
- [USAGE.md](USAGE.md)
- [CALENDAR.md](CALENDAR.md)
- [MCP.md](MCP.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
- [TELEMETRY.md](TELEMETRY.md)
- [DESIGN.md](DESIGN.md)
