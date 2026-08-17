# Nojoin Architecture Overview

This document provides a human-readable overview of how Nojoin fits together.

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

Dispatching that work is a blocking socket call to Redis, and the API dispatches from `async def` handlers, so an unreachable broker would otherwise stall the whole event loop rather than one request. Two things prevent that. Every dispatch reachable from a request handler goes through `backend/core/task_dispatch.py`, which runs the publish off the event loop, so a slow or unreachable broker delays only the request that dispatched. And the API gives up quickly: connect attempts are capped at two seconds and the API process caps publish and result-backend retries at one, so a dispatch against a dead broker fails in milliseconds and against an unreachable one in a few seconds instead of being unbounded. Workers keep Celery's generous defaults and dispatch inline, because a worker retrying to write a result has nothing waiting on it and no event loop to protect.

The bounds are set from measurement rather than taste. Before they existed, a dispatch against a broker refusing connections blocked for 19.04s and a concurrent request issued 50ms later took 18.99s; against a broker dropping packets the request had not returned after 70s and no concurrent request completed at all, with a theoretical worst case near 42 minutes of wedged event loop. Afterwards the same dispatch costs 0.03s refused and 6.03s unreachable, and during the latter 60 concurrent requests kept a sub-millisecond median. Two details make this easy to get wrong when editing `backend/celery_app.py`: the broker reads its transport options while the result backend reads top-level `redis_*` keys, and bounding the publish retry alone does nothing, because `send_task` subscribes the result backend to the task's channel before it ever publishes.

Per-user AI inference resolves to one of three usage models — install-wide Ollama, install-wide/BYOK API keys, or the per-user **CLI OAuth** mode, which routes through a user's own subscription using that provider's CLI in the `worker-io` lane. Two providers are supported: a Claude Pro/Max subscription driven by the Claude Agent SDK, and a ChatGPT subscription driven by the OpenAI Codex CLI. CLI OAuth degrades cleanly through the server's default provider chain, trying the primary provider first and the secondary after it, and is never load-bearing — the subscription path is unsanctioned by both providers and can be broken or enforced against without notice, so nothing is allowed to depend on it. See [SECURITY.md](SECURITY.md) for the trust boundary and the accepted risk.

Celery work is split across four resource lanes so a long recording finalise
never blocks lightweight tasks: a single-slot GPU lane (finalise, live ASR,
embeddings), a CPU lane (ffmpeg transcode, proxies, backups), an IO/LLM lane
(Meeting Edge, notes, chat, calendar sync) that also runs Celery Beat, and a
parse lane (document parsing and RAG index rebuilds). Routing
lives in `backend/celery_app.py` (`TASK_ROUTES`); see [DEPLOYMENT.md](DEPLOYMENT.md)
for pool sizing. To avoid reloading the live ASR model between segments, the GPU
lane keeps it resident while a capture is uploading and releases it when idle.
During finalise the meeting-intelligence step (notes, title, speaker suggestions)
is handed to the IO lane for non-local providers, so a network-bound LLM call
never occupies the GPU worker; local Ollama runs it inline.

Document parsing has its own lane because it is unbounded: there is no page
cap, so one large upload can hold a worker slot for a long time. On the IO lane
that would sit beside Meeting Edge and meeting chat and degrade a live meeting.
The lane runs the `worker-io` **image** rather than a new one, since visual
parsing may route through a subscription CLI whose binaries ship only there, so
it adds a container but no build and no image to scan.

### Document Parsing

An uploaded document is parsed into `document_pages`, one row per page, slide,
sheet, or heading-bounded section. Every format gets a structural pass first:
PDF text with reading-order sorting and table detection via PyMuPDF, and for
Office formats the underlying XML, which yields slide titles, table cells,
speaker notes, and the exact values behind native charts. That last point is why
no headless-office renderer is needed — a rendered chart would have to be
estimated from pixels, while the file itself holds the numbers.

When visual analysis is requested (the default), each page's images are sent to
the user's configured model: a rendered page for PDFs and images, the embedded
figures for Office formats. For a rendered page the model's output replaces the
text layer, having been given it and asked to improve on it; for a figure it
supplements the structural content, which the model never saw.

Parsing degrades through three tiers rather than two. Visual analysis is the
richest; below it sits local OCR (tesseract, in the worker image), which
transcribes glyphs with no provider involved and makes a scanned page searchable
on an install with no AI configured; below that is the format's own text layer.
OCR is skipped on any page whose text layer is already substantial, since it is
strictly worse at text than a real text layer, and it only ever supplements that
layer rather than replacing it. Each page records which tier produced it.

Pages are written as each completes, so a worker restart resumes from the first
missing page instead of repeating vision calls that were already paid for.
Vision requests fan out a few at a time. A provider that cannot accept images
raises `VisionUnsupportedError`, which downgrades the whole document to a
structural parse once and records a warning, rather than failing every page in
turn; an image upload has no text layer to fall back on, so that case is a real
error instead of an empty success.

Parsed pages feed three consumers: the RAG index (one chunk per page, split only
when a page exceeds the embedding window), the meeting-notes prompt, and the MCP
`get_documents` tool. Document text is untrusted input, and visual parsing
widens that — a model transcribing a page reproduces any instruction printed on
it — so both prompt sinks fence it in `<attached_document>` delimiters with an
explicit data-not-instructions rule.

### Meeting Analytics

Speaking-dynamics analytics for a recording — talk-time share, turn structure,
directional interruptions, turn-taking and response latency, a talk-share
timeline, and silence and overlap totals — are derived on read from the
canonical `transcript_utterances` rows rather than computed by the pipeline and
stored.

That is a deliberate trade. The derivation is arithmetic over a few thousand
rows, so per-request cost is negligible, and paying it buys two properties a
cached table would have to earn back with code. Every recording that predates
the feature has analytics immediately, with no migration, no backfill sweep,
and no reprocess — which matters because reprocess rebuilds the transcript and
clears hand-set speaker names, so making it the price of analytics would cost
users their corrections. And a speaker merge, rename, or transcript edit is
reflected on the next read, with no invalidation path that can be got wrong.
Speaker figures are grouped by `recording_speaker_id` precisely so a merge
redirects utterances onto the surviving row and the numbers follow.

The derivation lives in `backend/services/meeting_analytics/`, deliberately free
of ORM and ML imports below its query layer so it can run in the API process and
be tested as pure functions. Its thresholds — what counts as a turn boundary, an
interruption rather than turn-boundary bleed, or a measurable response gap — are
centralised in that package's `constants.py` for the same reason the speaker
thresholds are centralised in `backend/processing/embedding.py`: they are one
tuning decision, not several.

Two definitions are load-bearing anywhere the numbers are consumed. Per-speaker
talk time counts overlapping speech once for each speaker, so shares are
reported against total speech rather than against duration and the two
denominators are carried separately. And anything excluded from a statistic is
counted rather than dropped, so a median over four samples is distinguishable
from a median over ninety.

A third follows from the same principle and reshaped the surface. Nojoin's
transcripts hold no overlapping speech at all — the merge assigns each
transcribed segment one speaker from one mixed stream, so two people talking
at once is not representable in the transcript whatever happened in the room,
on live captures and imports alike. Every transcript-derived interruption
count is therefore zero by construction, and the surface carries **no
interruption counts at all**: overlapping speech is measured from the audio
instead (below), reported as overlap rather than interruption, because
attributing who cut across whom from a single channel is not defensible and
the interruption category itself is one human annotators barely agree on.
The evidence for both halves of that decision is in
[ANALYTICS_EVIDENCE.md](ANALYTICS_EVIDENCE.md).

Response behaviour at speaker changes is reported in three disclosed parts
rather than one filtered median, because the underlying timestamps carry
roughly a quarter-second of noise: **immediate handovers** (gaps under the
250ms measurement collar — real behaviour, the cross-language response mode
is 0-200ms, but unresolvable timing), a **reply-time median** over gaps the
timestamps can support (collar to five seconds), and **lapses** (longer
silences, which are resumptions rather than replies and once produced
30-second "reply times" on real recordings). Each population is counted.

Because every figure is attached to a speaker, the surface discloses when that
attribution is doubtful rather than presenting a confidently wrong number: a
structured warning is returned when several clusters hold a negligible share,
overlap is high, the speaker cap bound, or speakers are unnamed. Reasons are
codes, so the web client owns the wording and the MCP surface stays stable for
an assistant to branch on.

#### Delivery Descriptors

One analytics tier is measured rather than derived, and is therefore stored:
vocal delivery (speaking pace, pitch height and movement, loudness, within-turn
pausing), computed in
[backend/processing/delivery_descriptors.py](../backend/processing/delivery_descriptors.py)
and persisted on the transcript's `analytics_payload`.

It measures *how* people spoke and makes no claim about how they felt. No
emotion model is involved, and none should be added. That decision was
re-examined against the current literature and stands on stronger grounds
than when first made: speech-emotion recognition collapses on naturalistic
conversational speech (22-43% accuracy on every real conversational corpus
tested, against 90%+ on acted ones), its generalisation across speaker
populations is unproven, a wrong inference attached to a named colleague is
the worst output this surface can produce — and since February 2025 the EU AI
Act prohibits emotion inference from voice in the workplace for products
placed on the EU market, while explicitly excluding sentiment read from
transcript text, which is exactly the split Nojoin already makes. The full
evidence review is in [ANALYTICS_EVIDENCE.md](ANALYTICS_EVIDENCE.md). Every
figure here is an arithmetic property of the waveform, which is what lets the
interface present it without hedging and lets a user check it against the
audio.

Deliberately numpy and soundfile only, so it holds no model and never pulls
torch in. The pitch estimator is YIN's cumulative-mean-normalised difference
function — closed-form numpy, chosen after the original autocorrelation
picker measured a 4% gross-error rate against laryngograph ground truth,
enough to inflate the pitch-movement spread; the replacement halves every
product-level error and was validated on the same ground truth, clean and
after MP3 degradation. Pitch spread is reported in semitones rather than
hertz because hertz is not comparable between voices: the same expressive
range measures about twice as wide on a high voice as on a low one. The
plain-language readings beside the figures are calibrated against corpus
measurements rather than folk numbers — conversational speech runs at
160-200 words a minute by the measure Nojoin computes, not the oft-quoted
120-150 — and the pace bands are an English calibration, disclosed as such.

It runs on the **CPU lane**, dispatched at the end of `process_recording_task`
rather than inline. It needs no GPU, so holding the single-slot GPU lane to read
a WAV would delay the next meeting for nothing, and a failure must never reach a
recording that has otherwise finished. The same task backs the interface's
per-recording "Measure delivery" action, so a meeting recorded before the
feature existed takes exactly the path a new one does.

Channel selection is meaning-aware. For a browser capture the transcode contract
fixes channel 0 as shared audio and channel 1 as the microphone, so the
dominant channel per utterance identifies the capture source, using the same
dominance thresholds as the live lane. An imported stereo file's channels are
left and right, so those are downmixed instead — reading them as sources would
invent provenance. Because a remote voice has been through a codec and the far
end's gain control and a local microphone has not, loudness is reported as
comparable across speakers only when they shared a signal chain, and the
interface withholds the comparison rather than inviting a false one.

`DELIVERY_METHOD_VERSION` versions the extraction procedure exactly as
`EMBEDDING_METHOD_VERSION` versions a voiceprint, and for the same reason: a
figure produced by one procedure is not comparable with one produced by
another. The stored payload also carries the transcript event watermark it was
measured against, which is what makes staleness detectable without a second
column. Stale never means regenerate automatically — rereading a recording's
audio is work the user should ask for. A **method-version bump** is different:
that is Nojoin's own maintenance obligation, not user-triggered staleness, so
a bounded scheduled sweep (`remeasure_outdated_delivery_task`, mirroring the
voiceprint rebuild) re-measures a few outdated recordings per tick until the
library is comparable again.

Stored delivery figures also feed **cross-meeting baselines**: for a speaker
linked to a person, the analytics read derives that person's usual pace,
pitch movement, and pausing as medians across the user's other measured
meetings, and the interface says "faster than their usual across N meetings"
with the count always shown. Derived on read like the deterministic tier, so
a speaker merge or rename redirects the person link and the baseline follows;
only same-method-version figures are compared, and a person needs at least
three measured meetings before "their usual" is claimed. The wording is
delivery, never affect — a baseline framed as emotional state would be both
unsupported and, in the EU, prohibited.

#### Measured Overlap

Overlapping speech is measured from the recording's audio by
[backend/processing/audio_overlap.py](../backend/processing/audio_overlap.py)
and [backend/worker/tasks/analytics_overlap.py](../backend/worker/tasks/analytics_overlap.py),
reusing `pyannote/segmentation-3.0` — already a pipeline dependency for
boundary refinement, so this adds no model and no image growth. It runs on
the **GPU lane**, where the finalise pipeline keeps that model resident,
dispatched with the other post-processing follow-ups for new recordings and
from the same "Measure delivery" action for old ones. The result stores under
the `audio_overlap` key of `analytics_payload` with its own method version
and its status inside the block — it needs no column and no staleness story,
because it depends only on the audio, which never changes after processing.

What it reports is deliberately narrow: that people talked over each other,
for at least how long, and where in the meeting — never who overlapped whom.
The detection procedure was validated against AMI ground truth (precision
0.71-0.95, recall 0.62-0.85 with a 250ms collar, stable on far-field audio
and through 64kbps MP3), and its totals systematically underestimate, which
is why the interface presents the figure as a floor. Attribution and
"interruption" framing are withheld on evidence, not caution: speaker
attribution inside overlap has no published accuracy figure, and human
annotators agree on what counts as an interruption at kappa ~0.31. See
[ANALYTICS_EVIDENCE.md](ANALYTICS_EVIDENCE.md).

#### AI Analysis

The third analytics tier is neither derived nor measured: it is a model reading
the transcript. It reports the topics the meeting moved through and who drove
each, how each speaker's *words* read, which questions were asked and which went
unanswered, and who proposed, agreed with, or objected to each decision. It
lives in [backend/utils/meeting_analysis/](../backend/utils/meeting_analysis/)
(prompt and contract) and
[backend/worker/tasks/analytics_ai.py](../backend/worker/tasks/analytics_ai.py)
(the task), and stores under the `ai` key of the same `analytics_payload`.

The tier is **optional**, which is why it has its own `analytics_ai_status`
column rather than sharing the delivery tier's. It needs a fifth state the
measured tier does not have — `unavailable`, meaning no AI provider is
configured — and that is a normal condition on a healthy install rather than an
error. Sharing one column would also let an AI failure mark measured delivery as
broken, when the two are produced by different lanes from different inputs.

It is **never dispatched automatically**, unlike the delivery tier. One run is
one long call against the user's own AI quota, answering questions most meetings
are never asked, so it runs from the Analytics tab's button or the MCP
`analyse_meeting` tool. It is network-bound, so it runs on the **IO lane**,
matching how finalise already hands meeting intelligence there for non-local
providers. Both stored tiers therefore write one JSONB column from two lanes,
which is why each merges under a row lock through
[backend/utils/analytics_payload.py](../backend/utils/analytics_payload.py)
rather than assigning the column.

Three rules make a model's reading safe to attach to a named colleague, and all
three are enforced in the parser rather than trusted to the prompt. **Speakers
are an allowlist**: the model is given the meeting's own display names and
anything attributed to a name it was not given is discarded, because a name it
was not given is a name it invented. **Citations are verified, not accepted**: a
quote must appear in the transcript that was actually sent, normalised for case
and punctuation and checked against the whole corpus so a quote spanning a turn
boundary still passes, and its timestamp must fall inside the meeting. A
sentiment or decision item left with no surviving citation is **dropped**, not
shown — an unfalsifiable claim about a person is the worst output this feature
can produce, and decision ownership holds to that rule precisely because naming
who pushed back against a colleague is the most consequential thing here. And
**everything discarded is counted**, so a thin result is distinguishable from a
quiet meeting.

Sentiment is read from the words and nothing else. It is deliberately **not
fused** with the delivery descriptors above: those measure the sound of a voice,
these read language, and combining them into one score would manufacture a
confidence neither source supports. The two are presented as separate,
differently-sourced things, and no emotion model is involved in either.

The boundary with meeting notes is deliberate and stated to the model in the
prompt as well as enforced by what the schema can express. Notes remain the
record of *what* was decided and what happens next; this tier answers *who* —
who drove a topic, whose question went unanswered, who owned a decision — and
never authors a decision log. Consensus is reported as `stated`, `assumed`, or
`none`, and `assumed` means the decision merely went unchallenged, which the
interface must never render as agreement.

A very long meeting is truncated rather than refused, and the payload records
that it was along with how far the analysis reached. As with delivery, the
stored block carries a method version and a transcript event watermark, and
staleness shows a banner with a regenerate button rather than triggering another
call the user did not ask for.

### Web Client

The web client is responsible for:

- Dashboard workflows.
- Recordings workspace and transcript review.
- Meeting analytics.
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
- Holding a screen wake lock for the duration of a capture, re-taking it whenever the page returns to the foreground, since the browser drops the lock on every visibility change. This addresses device sleep only. Nothing in a web page can prevent the browser suspending a tab, or read whether Memory Saver is enabled. Nojoin does not warn about that up front: Chrome exempts tabs actively using the microphone or sharing a screen, which a recording tab is throughout, so the guidance lives in [CAPTURE.md](CAPTURE.md#chrome-memory-saver) as troubleshooting for the case it is actually needed.
- Comparing the audio the server holds against elapsed recording time and warning when they diverge. A tab suspended by the browser or the operating system stops feeding the recorder without raising an error, so coverage is the only signal that audio is being lost.

The captured figure comes from the backend, on `captured_audio_seconds` in the recording detail payload, summed from the transcoded segments and polled every 15 seconds while capture is open. The client cannot derive it: a segment carries slightly more than the nominal timeslice, because each roll flushes whatever accumulated while the recorder was stopping, and multiplying the sequence number by the timeslice under-counts by around 10% over an hour. That estimate produced coverage warnings on recordings that had lost nothing. The backend figure carries the opposite bias, running 2-3% high because each decoded segment includes codec priming that concatenation later trims, which is left uncorrected on purpose: it can only make a shortfall look smaller, never invent one.

The warning also carries a cause, because a shortfall means opposite things depending on why. `backend-unreachable` when the connectivity monitor reports the API down, and the queued audio will upload on reconnect. `tab-suspended` when the page was seen to thaw or the recorder watchdog caught it stalled, and the audio is gone. `unknown` when neither signal is present, with copy that describes the gap without diagnosing it. It is dismissible, and re-arms only when the shortfall grows materially past the value dismissed.

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

Voiceprints are only comparable with others produced by the same extraction procedure. `EMBEDDING_METHOD_VERSION` in [backend/processing/embedding_version.py](../backend/processing/embedding_version.py) records which one produced a stored vector, persisted on both `global_speakers.embedding_version` and `recording_speakers.embedding_version`. That module holds nothing but the version and its history, so a request path can reason about versions without importing torch and pyannote. Speaker identification and the duplicate-merge pass both refuse to score across versions; a cross-version cosine value resembles a similarity but is not one. Any change to how embeddings are produced must bump the version, and any new comparison site must check it — `embeddings_are_comparable` exists for that.

The versioning exists because the original extraction was measurably wrong rather than merely imperfect. Across this project's own library — 12 people, 2904 same-person pairs — 29% of same-person pairs scored below the 0.70 merge threshold and 37% below the 0.75 identification threshold, with a median of 0.803 but a tenth percentile of 0.363. Different-person pairs were clean by comparison, at a median of 0.073. The embedding space separated people perfectly well; same-person similarity collapsed whenever acoustic conditions changed, which is precisely the condition that makes the diariser split one person in the first place, so the safety net was weakest exactly where it was needed. `DUPLICATE_SPEAKER_MERGE_THRESHOLD` was therefore deliberately left at 0.70: the input to the comparison was wrong, not the threshold, and lowering it causes wrong merges, which a user finds harder to undo than wrong splits.

Stale voiceprints are repaired by `rebuild_voiceprints_task` rather than by comparing them anyway. The repair is automatic scheduled maintenance, running every six hours from Celery Beat and processing at most `AUTOMATIC_VOICEPRINT_REBUILD_LIMIT` (25) recordings per tick. It is bounded rather than operator-triggered because staleness is not a state a user can act on — it is a maintenance obligation Nojoin owes itself after changing its own extraction method — and because an unbounded re-extraction would run the embedding model over an entire library on hardware the user owns. Bounding each tick keeps the GPU lane responsive; a large library converges over several ticks. A stale voiceprint that cannot be re-extracted, because its audio is gone or its speaker owns no transcript segment, is cleared rather than kept: it could never be scored against anything, and leaving it would stall the sweep on a row it can never repair. Transient extraction failures are counted separately and leave the voiceprint in place so a later run can retry.

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
   as a compatibility projection. VAD regions
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
7. A Redis single-flight guard keyed on the recording admits one refresh at a
   time. Refreshes are triggered as the transcript grows, so during a live
   meeting a trigger arrives every few seconds while a run takes 20-45 seconds,
   and neither existing brake stops them overlapping: the staleness check reads
   a timestamp written only when a run finishes, and the signature check only
   suppresses input identical to a run already in flight, which a growing
   transcript never is. Surplus triggers are dropped rather than queued, since
   the next one carries fresher transcript, and each drop is recorded as a
   `meeting_edge_refresh_skipped` pipeline metric. The guard is a Redis key with
   a TTL rather than a database column so a worker killed mid-refresh releases
   it on expiry instead of wedging the feature, and it fails open so an
   unreachable broker cannot silently switch Meeting Edge off.

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

## Stall Detection

A live recording is the one workload where a stall is unrecoverable. The browser keeps counting wall-clock time while nothing reaches the server, and audio for that stretch is lost. Nojoin had no signal for it: an outage in August 2026 froze the API three times for about two minutes each, and the only evidence was requests that completed late.

The API therefore runs a watchdog (`backend/utils/stall_watchdog.py`) on its event loop, reporting two things:

- **Event-loop lag**, measured as the overshoot on a fixed sleep. Whatever froze the process shows up here, because the sleep cannot return on time: a blocked event loop, a throttled cgroup, and a host paging the process out are indistinguishable from inside and all detected. Reported as an `event_loop_stalled` pipeline metric when the overshoot exceeds the threshold.
- **Pressure Stall Information**, read from `/proc/pressure` at the moment lag is detected. This is what names the cause, since PSI's `avg10` window still reflects a stall that has just ended. Absent on non-Linux hosts and PSI-less kernels, where the lag signal survives without attribution. Sustained high pressure without a stall is reported separately as `host_pressure_high`, rate-limited so a persistently degraded machine does not bury the moment it started.

Nothing is sampled into the log on a schedule; a quiet system stays quiet. Tuning and switches are listed in [DEPLOYMENT.md](DEPLOYMENT.md#stall-detection).

## Anonymous Telemetry

An opt-out Celery Beat task on the IO lane sends one anonymous ping every six hours describing deployment scale, configuration shape, and feature adoption. The receiving service upserts on install id and UTC day, so the cadence buys resilience against a restart re-anchoring the beat interval rather than extra rows. It is identified only by a random install id held in `data/.install_id`, and carries no meeting content, names, or addresses.

State ownership is split deliberately so no two processes write the same value: the API is the only writer of the `telemetry_*` keys in `config.json`, the worker is the only writer of the Redis last-sent marker, and the install id file is write-once. The worker reloads configuration before every consent check, so an operator's opt-out takes effect on the next cycle rather than on the next container restart. Sending is best-effort with no retry, so a network fault can never escalate into repeated calls.

The receiving Cloudflare Worker and its D1 schema live in [telemetry/](../telemetry/). See [TELEMETRY.md](TELEMETRY.md).

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

The pipeline is gated rather than direct, and the ordering is the point. Immutable `version` and commit-`sha` tags are built and published first, each carrying a build-provenance attestation and an SBOM. Trivy then scans each image and fails the release on fixable CRITICAL or HIGH findings, with accepted exceptions recorded in `.github/trivyignore`. Each image is cosign-signed by digest, and a health-and-non-root smoke job boots the API and frontend. Only after all of that passes does a separate job advance the rolling `major.minor` and `latest` tags.

The consequence operators depend on is that a failing build can never advance the tag they pull by default. A briefly-exposed `vX.Y.Z` tag can exist for a failed run, but it is visibly attached to a failed pipeline and is never reachable through `latest`. Signing is keyless via GitHub OIDC, which binds each signature to the release workflow's identity and keeps a long-lived private key out of the threat model entirely. Third-party actions are pinned to commit SHAs and base images to `@sha256:` digests, so a mutable upstream tag cannot be repointed at unreviewed code after review. Anyone editing CI, the release workflow, or the Dockerfiles must preserve that pinning, the gate ordering, and the signing identity.

## Related Docs

- [CAPTURE.md](CAPTURE.md)
- [GETTING_STARTED.md](GETTING_STARTED.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [USAGE.md](USAGE.md)
- [CALENDAR.md](CALENDAR.md)
- [TELEMETRY.md](TELEMETRY.md)
- [DESIGN.md](DESIGN.md)
