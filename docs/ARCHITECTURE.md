# Nojoin Architecture Overview

This document provides a human-readable overview of how Nojoin fits together.

For product scope and longer-term feature intent, see [PRD.md](PRD.md).

## System at a Glance

Nojoin has three major parts:

1. A Dockerised backend that stores data and runs processing workloads.
2. A Next.js web client for browser capture, review, and administration.
3. Celery worker services that transcode live browser segments and run the transcription, diarisation, speaker, and AI processing pipeline.

## Core Components

### Backend

The backend is responsible for:

- API endpoints.
- Authentication and authorisation.
- Recording lifecycle management.
- Background task dispatch.
- Calendar sync orchestration.
- Release metadata and system operations.

The processing-heavy work runs in Celery workers rather than inside API endpoints.

Dispatching that work is a blocking socket call to Redis, and the API dispatches from `async def` handlers, so an unreachable broker would otherwise stall the whole event loop rather than one request. Two things prevent that. Every dispatch reachable from a request handler goes through `backend/core/task_dispatch.py`, which runs the publish off the event loop, so a slow or unreachable broker delays only the request that dispatched. And the API gives up quickly: connect attempts are capped at two seconds and the API process caps publish and result-backend retries at one, so a dispatch against a dead broker fails in milliseconds and against an unreachable one in a few seconds instead of being unbounded. Workers keep Celery's generous defaults and dispatch inline, because a worker retrying to write a result has nothing waiting on it and no event loop to protect. See [ADR-0007](adr/0007-bounded-fail-fast-task-dispatch.md).

Per-user AI inference resolves to one of three usage models — install-wide Ollama, install-wide/BYOK API keys, or the per-user **CLI OAuth** mode, which routes through a user's Claude subscription via the Claude Code CLI running in the `worker-io` lane (see [ADR-0002](adr/0002-cli-oauth-subscription-mode.md)). CLI OAuth degrades cleanly through the server's default provider chain (the primary provider first, then the secondary) and is never load-bearing.

Celery work is split across three resource lanes so a long recording finalise
never blocks lightweight tasks: a single-slot GPU lane (finalise, live ASR,
embeddings), a CPU lane (ffmpeg transcode, proxies, backups), and an IO/LLM lane
(Meeting Edge, notes, chat, calendar sync) that also runs Celery Beat. Routing
lives in `backend/celery_app.py` (`TASK_ROUTES`); see [DEPLOYMENT.md](DEPLOYMENT.md)
for pool sizing. To avoid reloading the live ASR model between segments, the GPU
lane keeps it resident while a capture is uploading and releases it when idle.
During finalise the meeting-intelligence step (notes, title, speaker suggestions)
is handed to the IO lane for non-local providers, so a network-bound LLM call
never occupies the GPU worker; local Ollama runs it inline.

### Web Client

The web client is responsible for:

- Dashboard workflows.
- Recordings workspace and transcript review.
- Speaker management.
- Notes, meeting chat, and document upload.
- User, admin, and system settings.
- Browser capture orchestration through `getDisplayMedia`, `getUserMedia`, Web Audio mixing, MediaRecorder segmenting, sequenced upload, live waveform state, pause/resume, and finalize controls. Mobile Chrome uses the same lifecycle with a microphone-only `getUserMedia` path.

The web client is the only live capture surface. Unsupported browsers retain review, playback, admin, and settings capabilities, but cannot start live recording.

### Browser Capture Stack

The browser capture stack is responsible for:

- Prompting for shared tab, window, or screen audio.
- Prompting for microphone access.
- Mixing shared audio and microphone audio in the browser on desktop, or recording microphone-only audio on mobile Chrome.
- Recording short WebM/Opus, Ogg/Opus, or MP4 audio slices and uploading them with session-cookie authentication.
- Preserving the browser-live source layout after worker transcode as 16 kHz, two-channel WAV: channel 0 is shared/system audio when available and channel 1 is microphone audio.
- Exposing analyser output to the live waveform UI.
- Moving recordings to `PAUSED` on real tab unload (pagehide/beforeunload) only, then requiring resume, stop-and-process, or discard before another capture starts. In-app page navigation does not pause capture.
- Bounding every stage of the stop sequence and reporting which stage it is on, so a stalled recorder or uploader degrades to a retryable finalize rather than leaving the recording unfinishable.
- Comparing stored audio against elapsed recording time and warning when they diverge. A tab suspended by the browser or the operating system stops feeding the recorder without raising an error, so coverage is the only signal that audio is being lost.

## Recording Flow

1. The browser authenticates through a Secure HttpOnly session cookie.
2. From the **Meet Now** card, the user clicks **Start Meeting** in Chrome on Windows, Linux, or macOS, another supported desktop Chromium browser, or Chrome on Android/iOS for microphone-only recording.
3. `/recordings/init` creates an `UPLOADING` recording for the current user. The same browser session is used for segment, pause, resume, discard, and finalize operations.
4. On desktop, the browser asks for shared tab/window/screen audio and microphone access, mixes those streams, and records short audio slices. On mobile Chrome, the browser asks for microphone access only and records microphone-only slices.
5. The browser uploads segments to `/recordings/{id}/segment?sequence=N` with monotonically increasing 0-based sequence numbers.
6. The worker transcodes each browser segment to 16 kHz, two-channel WAV and dispatches the live transcription lane. Channel 0 is shared/system audio when available and channel 1 is microphone audio.
7. Finalisation concatenates the completed WAV segments, queues backend processing, and triggers proxy generation. It accepts an `UPLOADING` or a `PAUSED` recording, so a capture whose browser runtime is gone can still be finalised from its uploaded segments without resuming first; missing sequences and pending transcodes are still refused with a retryable 409.
8. The web client shows a live capture or processing status workspace while the job runs.

If the user refreshes, closes, or navigates away from the Nojoin tab while recording (actual tab unload, not in-app navigation), the browser stops capture, drops only the in-memory tail, and asks the backend to mark the recording `PAUSED`. Uploaded segments remain available. On the next app load, Nojoin blocks new capture behind a mandatory modal offering resume, stop-and-process, or discard. Stop does not require a live browser runtime, so a recording in this state always has a route to processing.

Switching focus to another browser tab, window, or application does not pause capture. Navigating between pages within the Nojoin app also does not pause capture. Only a real Nojoin tab unload (pagehide/beforeunload) invokes the guarded pause path.

When a recording is active, a floating recording badge appears at the top-centre of the viewport showing the recording status, elapsed time, and pause, resume, and stop controls. Clicking the badge navigates to the recording detail page. The badge remains visible on every page except the recording detail page so the user never loses visibility of the active recording while navigating the app.

## Processing Pipeline

The normal backend processing path is:

1. Validation.
2. VAD and audio preprocessing.
3. Proxy creation for web playback.
4. Transcription via a pluggable engine under [backend/processing/engines/](../backend/processing/engines/) (Whisper by default, Parakeet or Canary via onnx-asr selectable sharing `OnnxAsrEngine`).
5. Pyannote diarisation, optionally bounded by the recording's `max_speakers`.
6. Phantom speaker filtering.
7. Merge, voiceprint extraction, and deterministic speaker resolution.
8. Rolling diarisation window reconciliation: completed rolling windows captured during the live lane are replayed to apply speaker boundary corrections to provisional live utterances.
9. Frame-level segmentation refinement: a second boundary-quality pass using `pyannote/segmentation-3.0` inspects boundary-flagged and long live-emitted utterances and re-splits them where the dense per-frame speaker activity map identifies a cleaner turn boundary than the rolling diarisation windows resolved.
10. Automatic meeting intelligence when an AI provider and model are configured.
  11. Automatic application of inferred speaker names to unresolved speakers, plus persistence of the meeting title and Markdown meeting notes. Applied suggestions are retained on the transcript as an audit trail.

### Speaker Cap And Voiceprint Versioning

`Recording.max_speakers` is an optional per-recording upper bound. `NULL` means auto-detect and is the default; that path passes no speaker keyword to pyannote at all, so it is unchanged from before the field existed. A set value is applied as pyannote's `max_speakers` and never as `num_speakers` — an exact count forces a split whenever the user overcounts, which is the over-clustering failure the field exists to prevent. It is settable at import, on the reprocess request, and throughout a live capture, since diarisation runs at stop time.

Voiceprints are only comparable with others produced by the same extraction procedure. `EMBEDDING_METHOD_VERSION` in [backend/processing/embedding_core.py](../backend/processing/embedding_core.py) records which one produced a stored vector, persisted on both `global_speakers.embedding_version` and `recording_speakers.embedding_version`. Speaker identification and the duplicate-merge pass both refuse to score across versions; a cross-version cosine value resembles a similarity but is not one. Stale voiceprints are repaired by `rebuild_voiceprints_task`, surfaced through `GET /speakers/voiceprints/method-status` and `POST /speakers/voiceprints/rebuild`, rather than by comparing them anyway. Any change to how embeddings are produced must bump the version. See [ADR-0005](adr/0005-versioned-voiceprint-extraction.md).

Two `pipeline_metric` stages make diarisation quality inspectable from a worker log. `final_diarization_speaker_stats` records per-cluster speech duration, segment count and share, plus overlapped speech and whether a cap bound. `speaker_merge_pass` is emitted on **every** run of the duplicate-merge pass, including runs that merge nothing and runs that could not score anything (which carry an explicit reason), and lists each pair's cosine score. A pass that merged nothing previously logged nothing and was indistinguishable from one that never ran.

A user can discard a recording at any in-flight stage: uploading, paused, queued, or processing. Discard is a single graceful operation that revokes the running Celery task with `terminate=True`, deletes every on-disk artefact, and removes the recording row, so no manual cancel-then-delete sequence is required. Terminating the task stops the worker from continuing the pipeline, and the worker's start-of-task cancellation guard prevents a revoked-but-requeued task from resuming work. Terminal recordings (processed, errored, or already removed) are deleted through the standard delete flow instead.

Per-user language preferences are resolved once through the shared backend language registry. The effective transcription language is propagated to live, catch-up, final, imported, and reprocessed ASR calls and included in ASR result hashes. Whisper receives an explicit language only when one is selected; Canary receives its supported source-language parameter; Parakeet remains multilingual auto-detection and therefore hashes as automatic language.

Generated-content language is independent from source-audio language. Manual notes generation, unified automatic meeting intelligence, standalone title generation, and secondary-provider fallback receive the same resolved output-language instruction. Prompt control text and JSON keys remain stable, while titles and Markdown content can be localized. The automatic intelligence contract accepts any non-empty top-level Markdown heading rather than requiring the English `# Meeting Notes` heading.

Manual user notes can be captured during recording or processing and are fed into both the automatic meeting-intelligence stage and the manual note-generation flow.

If AI configuration is missing, the recording still completes with transcript, diarisation, and deterministic speaker resolution intact. Automatic AI enhancement is skipped rather than failing the meeting. Manual `Generate Notes` and `Retry Speaker Inference` remain available once AI is configured.

A secondary LLM provider can be configured via the `SECONDARY_LLM_PROVIDER` environment variable. When set, all AI features (meeting intelligence, Meeting Edge, speaker inference, chat) automatically fall back to the secondary provider if the primary provider fails with any error, handled by `SecondaryLLMBackend`. The secondary provider has its own model, live model, and API key settings, configured independently. Fallback is transparent: the primary provider is tried first, and on failure the system logs a warning and retries with the secondary provider. If both fail, the primary provider's error is raised.

To cut token cost on repeated context, Meeting Chat and Meeting Edge lay out their prompts cache-first — the large, stable portion leads and the volatile part is sent last. Meeting Chat sends the meeting notes and full transcript as the system prompt (the Anthropic backend marks it with a `cache_control` breakpoint; OpenAI-compatible providers reuse the leading system message through automatic prefix caching), leaving only the conversation history and the user's question in the messages array. Meeting Edge splits its single prompt into a stable instruction/JSON-schema prefix and the volatile per-refresh context (rolling summary, recent transcript), and the Anthropic backend `cache_control`-marks that prefix. Caching is transparent to the user — it changes only how the request is framed for reuse, not what the model is asked — and simply yields no benefit when a provider or model does not support it.

Playback, transcript viewing, and export all operate on the full recording timeline without applying persisted trim offsets.

### Live Transcription Lane

While a recording is still uploading, a secondary lane produces provisional
transcript text so the web client can show progress before the full pipeline
runs:

1. Each segment upload endpoint dispatches a live transcription task
   (`backend/processing/live_transcribe.py`).
2. The task slices completed speech regions, transcribes them with the same
   engine selected by `transcription_backend` for final processing, writes
   canonical provisional utterances first, and refreshes `Transcript.segments`
   as a compatibility projection.
   VAD regions
   are padded and each region clip is prepended with a short rolling audio
   context window (`live/context.wav`) so the engine has acoustic run-up and
   word edges are not clipped; the engine output is then sliced back to the
   region.
3. The web client shows a single in-flight workspace with a live transcript
   panel, waveform, Meeting Edge guidance, notes, and processing visibility as
   soon as the recording is in flight. The panel polls
   `GET /transcripts/{id}/utterances` every three seconds for provisional
   utterances and is read-only. The recording detail payload continues to
   suppress in-flight transcript text and segments, so provisional text still
   never reaches recording cards, the sidebar, or exports; the utterances
   endpoint is the only in-flight transcript surface.
4. Live utterances carry no speaker. Every provisional utterance is written
   with the `UNKNOWN` label, because diarization runs only at finalize; speaker
   identity for live text is therefore resolved by the catch-up diarization and
   final pipeline passes rather than during capture. Transcript edits made by
   the user are treated as authoritative.
   What the lane does resolve per region is the *capture channel*: it compares
   per-channel RMS to decide whether the microphone or the shared system audio
   dominated, and persists that reading in the utterance's
   `confidence_payload`. Utterance reads expose it as `source_channel`
   (`microphone`, `system`, or null), which the live panel uses to label audio
   provenance. It is populated only when the evidence was unambiguous, and it
   describes the channel rather than a person: browser capture always produces
   two channels, and a capture with no shared audio leaves channel 0 silent, so
   a microphone-only capture reads as clear microphone dominance for every
   region including speech from everyone else in the room.
5. After new live segments land, the API/worker layer best-effort dispatches a
   separate `refresh_meeting_edge_task`. Best-effort means the dispatch failing
   is logged and swallowed rather than failing the request that triggered it:
   the refresh is derived guidance that the next mutation or live segment
   recomputes from scratch, whereas failing the request would discard the edit
   the user actually made. That task builds a bounded recent
   transcript window, reuses the previous run's dedicated rolling summary (a
   model-maintained 150-300 word running context of decisions, open threads,
   and action items, falling back to the short displayed summary for older
   payloads) as rolling context, passes the previously suggested questions and
   points back to the model so still-relevant items are retained and stale or
   duplicate ones are replaced, folds in user-authored notes, optional user
   focus text, and linked calendar context, then requests a strict JSON
   response from the configured LLM provider. Provider-native JSON output
   modes are used where available (Gemini `response_mime_type`, OpenAI
   `response_format` with a plain retry for incompatible OpenAI-compatible
   endpoints, Anthropic assistant prefill, Ollama `format: json`), with the
   tolerant fenced/inline JSON parser retained as a fallback. Changing the
   Meeting Edge context-level slider or the enable toggle also dispatches a
   refresh for the user's in-flight recordings, and the context level is part
   of the refresh source signature so slider changes take effect immediately.
6. Meeting Edge uses the same configured provider as the rest of Nojoin AI, but
   resolves a separate provider-specific live model when one is set. If no
   Meeting Edge model is configured for that provider, the worker falls back to
   the provider's main model instead of failing the live guidance path.

Segments are numbered sequentially starting at 0 but uploaded concurrently, so the lane uses
a **sequence-gated buffer**. Each task reads `next_expected` from a per-recording
`live/state.json`; a task whose segment is ahead of `next_expected` returns
immediately (its WAV waits on disk), and only the task holding `next_expected`
drains the contiguous run of segments present on disk. Audio from the trailing,
not-yet-complete utterance is **carried over** in `live/buffer.wav` and joined
to the next run, so an utterance split across a segment boundary is normally
transcribed once as a whole. If speech continues past the live forced-emission
window, the lane force-emits continuous speech after about 8 seconds, updating
the current speech region and starting a new live segment.

Browser-live audio window manifests track two independent processing lanes. The
ASR lane records whether live or catch-up ASR consumed the window audio. The
diarisation lane records rolling or catch-up speaker-window work for the active
diarisation configuration and completed window result. The legacy window
`status` field remains a compatibility projection; new logic should inspect the
lane-specific ASR and diarisation fields. Operator-facing recording pages now
surface only high-level recording progress plus Meeting Edge guidance while a
recording is still in flight.

The live lane is best-effort: any failure is logged, the lane still advances,
and nothing is re-raised. When the recording finalises, `process_recording_task`
promotes canonical live and catch-up transcript state first, fills only missing
durable spans, replays completed rolling diarisation windows when that is
sufficient, preserves authoritative user edits, and only falls back to a
whole-recording ASR or diarisation rerun when coverage is missing,
confidence remains too low, or the user explicitly requests reprocessing with a
different engine. A different transcription engine is reserved for explicit
manual reprocessing after the user changes the transcription engine in Settings.

Final processing may reuse live transcript text and source-channel speaker
authority only after a stable utterance id match or a clear one-to-one time
overlap match. It must not align live and final segments by array index. When a
merged, split, or low-confidence span is ambiguous, final processing keeps the
final ASR/diarisation output and records live evidence in alignment metadata
instead of silently applying it to the wrong time span. Manual text and speaker
locks remain authoritative.

### Startup Canonical Cutover

The unified pipeline now assumes a container-level startup cutover for older
meetings rather than a frontend-driven migration workflow.

1. `backend/entrypoint.sh` runs Alembic through `backend.startup_migrations`.
2. The same entrypoint then runs `backend.startup_canonical_cutover` before the
   API process starts.
3. That cutover acquires a database advisory lock, sweeps any recordings whose
   `pipeline_generation` marker is still unset, and classifies each one into a
   backend-only compatibility state.
4. Successfully canonicalised historical meetings are marked `legacy_backfilled`
   and remain viewable through the compatibility projection.
5. Historical meetings that were still in flight during upgrade or that cannot
   be canonicalised safely are marked `legacy_reprocess_required` and normalized
   for explicit reprocess instead of continuing to rely on legacy mutation
   paths.
6. Only meetings created or explicitly rebuilt through the unified pipeline are
   marked `unified` and treated as fully supported for transcript and speaker
   mutation flows.

## Anonymous Telemetry

An opt-out daily Celery Beat task on the IO lane sends one anonymous ping describing deployment scale, configuration shape, and feature adoption. It is identified only by a random install id held in `data/.install_id`, and carries no meeting content, names, or addresses.

State ownership is split deliberately so no two processes write the same value: the API is the only writer of the `telemetry_*` keys in `config.json`, the worker is the only writer of the Redis last-sent marker, and the install id file is write-once. The worker reloads configuration before every consent check, so an operator's opt-out takes effect on the next cycle rather than on the next container restart. Sending is best-effort with no retry, so a network fault can never escalate into repeated calls.

The receiving Cloudflare Worker and its D1 schema live in [telemetry/](../telemetry/). See [TELEMETRY.md](TELEMETRY.md) and [ADR-0004](adr/0004-anonymous-opt-out-telemetry.md).

## Calendar Flow

1. An admin configures Google and/or Microsoft OAuth credentials for the installation.
2. End users connect their own accounts from the Personal settings area.
3. Nojoin syncs selected calendars into stored dashboard-facing event data, including each event's description and attendee list.
4. The dashboard renders month markers, agenda items, next-event summaries, and colour-coded sources, combining synced calendar events with unlinked Nojoin recordings.
5. Recordings carry a nullable `calendar_event_id`; a recording is auto-linked to a confidently overlapping calendar event during processing (or linked manually), and the linked event enriches notes and speaker prompts while suppressing the recording's standalone dashboard calendar item.
6. Sync runs incrementally on the worker's embedded Celery Beat scheduler: every connected account with a selected calendar refreshes on a 15-minute cadence using each provider's change cursor (Google `syncToken`, Microsoft Graph `delta`). When an admin enables live sync and the instance is publicly reachable over HTTPS, Nojoin also registers Google `events.watch` channels and Microsoft Graph subscriptions, so changes arrive by webhook and enqueue an immediate incremental sync; the 15-minute schedule remains the always-on fallback.

## Authentication Model

Nojoin uses different auth shapes for different clients:

- **Browser traffic**: Secure HttpOnly session cookies. State-changing browser requests authenticated by that session must originate from the trusted Nojoin web origin, using standard `Origin` or `Referer` validation rather than relying only on `SameSite` and CORS.
- **Non-browser API clients**: Explicit bearer tokens.
- **MCP connector clients**: OAuth 2.1 bearer tokens (token type `mcp`) minted by Nojoin's built-in authorization server. These tokens authenticate only the `/mcp` endpoint, never the general API, and are contained by the same `token_version` and denylist machinery as sessions. The tool surface is read-only apart from a small set of scope-gated, additive write tools (People import, speaker naming, and note append). See [MCP.md](MCP.md).
- **Browser recording operations**: Session-authenticated init, segment, pause, resume, discard, and finalize calls owned by the current user.
- **Legacy native-helper routes**: Retired routes return structured `410 Gone` responses that point operators to [CAPTURE.md](CAPTURE.md).

Forced password rotation is enforced server-side. Flagged users can only reach their self-profile, password update flow, and logout until the rotation is complete.

## Storage and Persistence

- **PostgreSQL** stores metadata, transcripts, speakers, tasks, calendar state, and user settings.
- **Redis** supports Celery and related queue or cache operations.
- **Recordings storage** holds source audio, derived proxy assets, and related files on disk.
- **Config files** store system-wide configuration, while sensitive material is encrypted or otherwise handled separately where appropriate.

## Release Model

Nojoin follows a unified release model:

- Git tags in the form `vX.Y.Z` drive published releases.
- Docker images are published to GHCR.
- The application surfaces release metadata primarily from GitHub Releases.

## Related Docs

- [CAPTURE.md](CAPTURE.md)
- [GETTING_STARTED.md](GETTING_STARTED.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [USAGE.md](USAGE.md)
- [CALENDAR.md](CALENDAR.md)
- [TELEMETRY.md](TELEMETRY.md)
- [PRD.md](PRD.md)
