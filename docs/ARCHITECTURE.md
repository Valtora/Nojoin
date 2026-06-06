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
- Moving recordings to `PAUSED` on real tab unload (pagehide/beforeunload) only, then requiring resume or discard before another capture starts. In-app page navigation does not pause capture.

## Recording Flow

1. The browser authenticates through a Secure HttpOnly session cookie.
2. From the **Meet Now** card, the user clicks **Start Meeting** in Chrome on Windows, Linux, or macOS, another supported desktop Chromium browser, or Chrome on Android/iOS for microphone-only recording.
3. `/recordings/init` creates an `UPLOADING` recording for the current user. The same browser session is used for segment, pause, resume, discard, and finalize operations.
4. On desktop, the browser asks for shared tab/window/screen audio and microphone access, mixes those streams, and records short audio slices. On mobile Chrome, the browser asks for microphone access only and records microphone-only slices.
5. The browser uploads segments to `/recordings/{id}/segment?sequence=N` with monotonically increasing 0-based sequence numbers.
6. The worker transcodes each browser segment to 16 kHz, two-channel WAV and dispatches the live transcription lane. Channel 0 is shared/system audio when available and channel 1 is microphone audio.
7. Finalisation concatenates the completed WAV segments, queues backend processing, and triggers proxy generation.
8. The web client shows a live capture or processing status workspace while the job runs.

If the user refreshes, closes, or navigates away from the Nojoin tab while recording (actual tab unload, not in-app navigation), the browser stops capture, drops only the in-memory tail, and asks the backend to mark the recording `PAUSED`. Uploaded segments remain available. On the next app load, Nojoin blocks new capture behind a mandatory resume-or-discard modal.

Switching focus to another browser tab, window, or application does not pause capture. Navigating between pages within the Nojoin app also does not pause capture. Only a real Nojoin tab unload (pagehide/beforeunload) invokes the guarded pause path.

When a recording is active, a floating badge appears at the bottom of the viewport showing the recording status, elapsed time, and pause/resume/stop controls. This badge remains accessible from any page in Nojoin so the user never loses visibility of the active recording.

## Processing Pipeline

The normal backend processing path is:

1. Validation.
2. VAD and audio preprocessing.
3. Proxy creation for web playback.
4. Transcription via a pluggable engine (Whisper by default, Parakeet or Canary via onnx-asr selectable).
5. Pyannote diarisation.
6. Phantom speaker filtering.
7. Merge, voiceprint extraction, and deterministic speaker resolution.
8. Rolling diarisation window reconciliation: completed rolling windows captured during the live lane are replayed to apply speaker boundary corrections to provisional live utterances.
9. Frame-level segmentation refinement: a second boundary-quality pass using `pyannote/segmentation-3.0` inspects boundary-flagged and long live-emitted utterances and re-splits them where the dense per-frame speaker activity map identifies a cleaner turn boundary than the rolling diarisation windows resolved.
10. Automatic meeting intelligence when an AI provider and model are configured.
  11. Persistence of unresolved speaker suggestions, meeting title, and Markdown meeting notes.

Manual user notes can be captured during recording or processing and are fed into both the automatic meeting-intelligence stage and the manual note-generation flow.

If AI configuration is missing, the recording still completes with transcript, diarisation, and deterministic speaker resolution intact. Automatic AI enhancement is skipped rather than failing the meeting. Manual `Generate Notes` and `Retry Speaker Inference` remain available once AI is configured.

A secondary LLM provider can be configured via the `SECONDARY_LLM_PROVIDER` environment variable. When set, all AI features (meeting intelligence, Meeting Edge, speaker inference, chat) automatically fall back to the secondary provider if the primary provider fails with any error. The secondary provider has its own model, live model, and API key settings, configured independently. Fallback is transparent: the primary provider is tried first, and on failure the system logs a warning and retries with the secondary provider. If both fail, the primary provider's error is raised.

Playback, transcript viewing, and export all operate on the full recording timeline without applying persisted trim offsets.

### Live Transcription Lane

While a recording is still uploading, a secondary lane produces provisional
transcript text so the web client can show progress before the full pipeline
runs:

1. Each segment upload endpoint dispatches a live transcription task
   (`backend/processing/live_transcribe.py`).
2. The task slices completed speech regions, transcribes them with the same
   engine selected by `transcription_backend` for final processing, assigns
   provisional live speaker identities, writes canonical provisional utterances
   first, and refreshes `Transcript.segments` as a compatibility projection.
   VAD regions
   are padded and each region clip is prepended with a short rolling audio
   context window (`live/context.wav`) so the engine has acoustic run-up and
   word edges are not clipped; the engine output is then sliced back to the
   region.
3. The web client shows a single in-flight workspace with waveform, Meeting
   Edge guidance, notes, and processing visibility as soon as the recording is
   in flight. The page no longer exposes provisional live transcript text,
   even though the backend live lane still emits it internally for Meeting
   Edge and later processing reuse.
4. Live speaker assignment uses online voice embeddings. Matching regions are
   merged into stable `LIVE_XX` speaker labels; short or embedding-less regions
   reuse the most recent stable label instead of creating new speaker churn.
   Live speaker names and transcript edits made by the user are treated as
   authoritative.
5. After new live segments land, the API/worker layer best-effort dispatches a
   separate `refresh_meeting_edge_task`. That task builds a bounded recent
   transcript window, reuses the previous Meeting Edge summary as rolling
   context, folds in user-authored notes, optional user focus text, and linked
   calendar context, then requests a strict JSON response from the configured
   LLM provider.
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
window, the lane emits the current speech region and starts a new live segment.

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
4. Successfully canonicalized historical meetings are marked `legacy_backfilled`
   and remain viewable through the compatibility projection.
5. Historical meetings that were still in flight during upgrade or that cannot
   be canonicalized safely are marked `legacy_reprocess_required` and normalized
   for explicit reprocess instead of continuing to rely on legacy mutation
   paths.
6. Only meetings created or explicitly rebuilt through the unified pipeline are
   marked `unified` and treated as fully supported for transcript and speaker
   mutation flows.

## Calendar Flow

1. An admin configures Google and/or Microsoft OAuth credentials for the installation.
2. End users connect their own accounts from the Personal settings area.
3. Nojoin syncs selected calendars into stored dashboard-facing event data, including each event's description and attendee list.
4. The dashboard renders month markers, agenda items, next-event summaries, and colour-coded sources, combining synced calendar events with unlinked Nojoin recordings.
5. Recordings carry a nullable `calendar_event_id`; a recording is auto-linked to a confidently overlapping calendar event during processing (or linked manually), and the linked event enriches notes and speaker prompts while suppressing the recording's standalone dashboard calendar item.

## Authentication Model

Nojoin uses different auth shapes for different clients:

- **Browser traffic**: Secure HttpOnly session cookies. State-changing browser requests authenticated by that session must originate from the trusted Nojoin web origin, using standard `Origin` or `Referer` validation rather than relying only on `SameSite` and CORS.
- **Non-browser API clients**: Explicit bearer tokens.
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
- [PRD.md](PRD.md)
- [AGENTS.md](AGENTS.md)
