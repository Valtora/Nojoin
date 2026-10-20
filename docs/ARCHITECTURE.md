# Nojoin Architecture Overview

This document provides a human-readable overview of how Nojoin fits together.

## System at a Glance

Nojoin has three major parts:

1. A Dockerised backend that stores data and runs processing workloads.
2. A Next.js web client for browser capture, review, and administration.
3. Celery worker services that run the transcription, diarisation, speaker, and AI processing pipeline.

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

Without the bounds, a dispatch against a broker dropping packets could wedge the event loop for minutes; with them it costs 0.03s against a refused connection and about 6s against an unreachable broker, while concurrent requests stay responsive. Two details make this easy to get wrong when editing `backend/celery_app.py`: the broker reads its transport options while the result backend reads top-level `redis_*` keys, and bounding the publish retry alone does nothing, because `send_task` subscribes the result backend to the task's channel before it ever publishes.

Per-user AI inference resolves to one of three usage models — install-wide Ollama, install-wide/BYOK API keys, or the per-user **CLI OAuth** mode, which routes through a user's own subscription using that provider's CLI in the `worker-io` lane. Two providers are supported: a Claude Pro/Max subscription driven by the Claude Agent SDK, and a ChatGPT subscription driven by the OpenAI Codex CLI. CLI OAuth degrades cleanly through the server's default provider chain, trying the primary provider first and the secondary after it, and is never load-bearing — the subscription path is unsanctioned by both providers and can be broken or enforced against without notice, so nothing is allowed to depend on it. See [SECURITY.md](SECURITY.md) for the trust boundary and the accepted risk.

Celery work is split across four resource lanes so a long recording finalise
never blocks lightweight tasks: a single-slot GPU lane (finalise, live ASR,
embeddings), a CPU lane (proxies, backups, and the transcode of segments from
clients older than the FLAC capture path), an IO/LLM lane
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

Speaking-dynamics analytics for a recording (talk-time share, turn structure,
turn-taking and response latency, a talk-share timeline, and silence and
overlap totals) are derived on read from the
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
be tested as pure functions. Its thresholds, what counts as a turn boundary or a
measurable response gap, are
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
silences, which are resumptions rather than replies). Each population is
counted.

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
torch in. A recording soundfile cannot open is first decoded with ffmpeg to a
temporary 16 kHz WAV that keeps its channels: a browser capture made before the
FLAC capture path is stored in the container MediaRecorder produced (WebM/Opus,
or MP4/AAC on older Safari), and imports such as M4A, AAC, MP4 and WMA keep
theirs. A browser capture made since is FLAC, which soundfile reads directly. 16 kHz is the rate the
pitch estimator was validated at, and it keeps the decoded copy of a long
two-channel capture to about a third of its native size.
The pitch estimator is YIN's cumulative-mean-normalised difference
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

Channel selection is meaning-aware. For a browser capture the capture contract
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
column. Stale figures are re-measured when the Analytics tab is next read,
not when the edit lands: rereading a recording's audio is work worth spending
only on a meeting someone is looking at, and the read that would otherwise show
stale figures is that moment. The read queues the same CPU-lane task the button
dispatches, reports the tier as generating so the tab polls for the result, and
holds a once-per-window claim per recording so the tab's own polling cannot
queue the run again before the worker marks it generating. A per-user setting
(`auto_remeasure_delivery`, on by default) turns this off, after which the tab
shows the figures as stale with the button; a measurement that
ended in error is never retried by a read either. Editing a transcript nobody
reopens costs nothing. A **method-version bump** is different: that is Nojoin's
own maintenance obligation, not user-triggered staleness, so a bounded
scheduled sweep (`remeasure_outdated_delivery_task`, mirroring the voiceprint
rebuild) re-measures a few outdated recordings per tick until the library is
comparable again.

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
running `pyannote/segmentation-3.0`. The model ships in the bundled models,
[backend/processing/segmentation_model.py](../backend/processing/segmentation_model.py)
loads and caches it, and overlap analytics is its only consumer. It runs on
the **GPU lane** with the other pyannote models, dispatched with the other
post-processing follow-ups for new recordings and from the same "Measure
delivery" action for old ones. The result stores under
the `audio_overlap` key of `analytics_payload` with its own method version
and its status inside the block — it needs no column and no staleness story,
because it depends only on the audio, which never changes after processing.
A recording soundfile cannot open is decoded with ffmpeg to a temporary 16 kHz
mono WAV first, the rate and layout the model works at.

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

It runs on request by default, unlike the delivery tier. One run is one long
call against the user's own AI quota, answering questions most meetings are
never asked, so it runs from the Analytics tab's button or the MCP
`analyse_meeting` tool. Two things bound what that can cost. An administrator
may cap the runs a user starts per UTC day (`ai_analysis_daily_limit` in
`config.json`, with a per-user override on the user record); every run is
logged in `ai_analysis_runs` where it is queued, the API refuses a run past
the cap with 429 and the message the tab shows, and the read reports the
budget so the tab can say so before anyone clicks. With that cap in place a
user may also opt in to automatic analysis (`auto_analyse_meetings`, off by
default), which queues the tier from the post-processing follow-ups within
the same budget. It is network-bound, so it runs on the **IO lane**,
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

### Exports

`GET /api/v1/transcripts/{id}/export` builds one file from any of the meeting
notes, the analytics and the transcript, as PDF, DOCX or plain text. The work
lives in `backend/services/exports/` and is split in two.

`builder.py` decides what an export says. It turns a meeting into an
`ExportDocument`, a tree of neutral blocks: headings, paragraphs, lists, tables,
stat tiles, callouts, charts and transcript lines. Notes are parsed from their
stored Markdown with `markdown-it-py`. Analytics come from
`read_recording_analytics`, the function the Analytics tab's own read uses, so a
file cannot report different figures from the tab, and an export never
dispatches a measurement. The wording and the plain-language readings are ports
of the tab's components (`formatDuration.ts`, `deliveryInsights.ts`,
`AttributionWarning.tsx` and the AI panels), with tests that pin the parity.

One renderer per format decides how each block looks: ReportLab for PDF,
python-docx for Word, and plain text. Charts are laid out once as shapes in
`charts.py` and painted twice, as vector graphics in the PDF and as a PNG in
Word, which cannot take ReportLab's drawings. Rendering runs in a worker thread,
because laying out a long transcript takes seconds and would otherwise stall
every request the API process is serving.

Two details are easy to miss. Automatic speaker colours exist only in the
browser, so the export dialog sends the colour map it is showing and
`speaker_colors.py` resolves it, falling back to saved colours and then to the
chart slots for a caller that sends none. And Word substitutes any font it
lacks, so the DOCX renderer embeds Geist Regular and Bold as obfuscated font
parts (ECMA-376 Part 1, section 17.8.1). The TTF files and their SIL Open Font
License live in `backend/services/exports/assets/fonts/`.

PDFs were drawn by markdown-pdf until issue #419. Its HTML layout repainted each
table header row's background at the same position on every later page, over
whatever text was there, so a regression test inspects every page after a table.

### Web Client

The web client is responsible for:

- Dashboard workflows.
- Recordings workspace and transcript review.
- Meeting analytics.
- Speaker management.
- Notes, meeting chat, and document upload.
- User, admin, and system settings.
- Browser capture orchestration through `getDisplayMedia`, `getUserMedia`, Web Audio mixing, an AudioWorklet tap that segments and FLAC-encodes the mix, sequenced upload with a queue kept in IndexedDB, live waveform state, pause/resume, and finalize controls. Mobile Chrome uses the same lifecycle with a microphone-only `getUserMedia` path.

The web client is the only live capture surface. Unsupported browsers retain review, playback, admin, and settings capabilities, but cannot start live recording.

#### Connectivity Monitor

Backend reachability is a state machine in `frontend/src/lib/connectivity/`, and the thing it exists to avoid is claiming an outage that is not happening. Every observed success is proof of life, whether it came from real API traffic, a health poll or an idle liveness probe, so a page doing steady work never probes at all. Only the dedicated prober can escalate, and only after the last success has gone stale and three of its probes have failed.

Two rules keep that threshold meaning what it says. **Probes are serialised**: a tick will not start one while another is in flight. A tick awaiting its probe holds no timer, so without that rule every failing request would schedule another tick 500ms later and each would start another probe; a tab whose connection stalls fails a burst of requests at once, and would deliver several times the threshold in one go rather than confirm it over fifteen seconds. **A probe that outlasts its own timeout by a wide margin is not counted**, because a frozen tab suspends the abort timer along with the request and thaws them together, cancelling the fetch without ever having raced it against the network. Returning to the foreground clears the failure streak for the same reason: evidence gathered while the page was not being run is not evidence. The `resume` event cannot be used for any of this, since it is advisory and not dispatched on every Chromium build (issue #166), so the elapsed wall clock is the signal.

This matters beyond the toast. A false `unreachable` also mislabels an audio-coverage shortfall as `backend-unreachable`, which promises the queued audio will upload on reconnect, when the real cause was the suspension that lost it.

### Browser Capture Stack

The browser capture stack is responsible for:

- Prompting for shared tab, window, or screen audio.
- Prompting for microphone access.
- Mixing shared audio and microphone audio in the browser on desktop, or recording microphone-only audio on mobile Chrome.
- Taking the two-channel mix from an AudioWorklet tap (`frontend/public/audio-worklets/pcm-tap.js`), resampling it to 16 kHz with a windowed-sinc filter, cutting it into segments of exactly 32,000 frames by counting samples, and encoding each as FLAC (`frontend/src/lib/capture/pcm/`). Nothing restarts between segments, so they join with no gap. The MediaRecorder this replaced was stopped and restarted every 2 s, which dropped about 28 ms at every restart and left WebM segments whose headers understated their own length (issue #417).
- Uploading segments with session-cookie authentication, one at a time and in order, so the server never holds a later segment without the one before it. Each segment is written to IndexedDB when it is recorded and removed when the server acknowledges it, so a closed tab keeps what it had not sent; the next resume or stop-and-process in the same browser uploads it first.
- Preserving the browser-live source layout as 16 kHz, two-channel WAV, decoded from each uploaded FLAC segment by the API: channel 0 is shared/system audio when available and channel 1 is microphone audio.
- Exposing analyser output to the live waveform UI.
- Moving recordings to `PAUSED` on real tab unload (pagehide/beforeunload) only, then requiring resume, stop-and-process, or discard before another capture starts. In-app page navigation does not pause capture.
- Bounding every stage of the stop sequence and reporting which stage it is on, so a stalled recorder or uploader degrades to a retryable finalize rather than leaving the recording unfinishable.
- Holding a screen wake lock for the duration of a capture, re-taking it whenever the page returns to the foreground, since the browser drops the lock on every visibility change. This addresses device sleep only. Nothing in a web page can prevent the browser suspending a tab, or read whether Memory Saver is enabled. Nojoin does not warn about that up front: Chrome exempts tabs actively using the microphone or sharing a screen, which a recording tab is throughout, so the guidance lives in [CAPTURE.md](CAPTURE.md#chrome-memory-saver) as troubleshooting for the case it is actually needed.
- Comparing the audio the server holds against elapsed recording time and warning when they diverge. A tab suspended by the browser or the operating system stops feeding the recorder without raising an error, so coverage is the only signal that audio is being lost.

The captured figure comes from the backend, on `captured_audio_seconds` in the recording detail payload, summed from the decoded segments and polled every 15 seconds while capture is open. It is exact: every full segment is 2.000 s, and only a segment ended by a pause or a stop is shorter. The server's figure is still the one that counts, because a segment the browser sent but the server set aside as corrupt is not audio the meeting has.

The shortfall is net of the uploader's own queue. Segments recorded but not yet acknowledged are audio the browser is holding, not audio that has been lost, and a server that stops answering for two minutes leaves exactly two minutes of them; counting that as missing told a user their meeting was being lost while it sat in memory waiting to upload. The queue is measured from the durations of the segments in it, and it is named separately in the copy so a queue that never drains is still visible. A queue longer than 30 seconds is also shown on the recording controls while capture runs, and a stop that is still sending it says so instead of looking paused.

The warning also carries a cause, because a shortfall means opposite things depending on why. `backend-unreachable` when the connectivity monitor reports the API down, and the queued audio will upload on reconnect. `tab-suspended` when the page was seen to thaw or the recorder watchdog caught it stalled, and the audio is gone. `unknown` when neither signal is present, with copy that describes the gap without diagnosing it. An outage that has just ended still counts, for five minutes: the check polls every fifteen seconds and the shortfall persists while the queue drains, so classifying on the live status alone answered `unknown` for the very case the classifier exists to name. It ranks below a freeze, which is direct evidence the browser stopped recording where an outage is evidence it did not. It is dismissible, and re-arms only when the shortfall grows materially past the value dismissed.

## Recording Flow

1. The browser authenticates through a Secure HttpOnly session cookie.
2. From the **Meet Now** card, the user clicks **Start Meeting** in Chrome on Windows, Linux, or macOS, another supported desktop Chromium browser, or Chrome on Android/iOS for microphone-only recording.
3. `/recordings/init` creates an `UPLOADING` recording for the current user. The same browser session is used for segment, pause, resume, discard, and finalize operations.
4. On desktop, the browser asks for shared tab/window/screen audio and microphone access, mixes those streams, and cuts the mix into 2 s FLAC segments at 16 kHz. On mobile Chrome, the browser asks for microphone access only, and the shared channel is silent.
5. The browser uploads segments to `/recordings/{id}/segment?sequence=N` with monotonically increasing 0-based sequence numbers.
6. The API decodes each FLAC segment in process into 16 kHz, two-channel WAV, registers it (see [Chunk Registration](#chunk-registration)), and dispatches the live transcription lane. Channel 0 is shared/system audio when available and channel 1 is microphone audio. A segment that will not decode is quarantined as a `.corrupt` tombstone and the upload still succeeds, so one bad segment costs a 2 s gap instead of pausing the meeting. WebM, Ogg and MP4 segments from a tab still running an older build go to the CPU worker for an ffmpeg transcode first, as before.
7. Finalisation rebuilds every chunk offset and window manifest once from the segments on disk, seals the cursor, joins the segment WAVs in order into a FLAC master with soundfile (4.4 s for a 2 h 17 m meeting, against 28 s for ffmpeg's concat demuxer over the same 4,164 files), takes the duration from the frames written, queues backend processing, and triggers proxy generation. It accepts an `UPLOADING` or a `PAUSED` recording, so a capture whose browser runtime is gone can still be finalised from its uploaded segments without resuming first; missing sequences and pending transcodes are still refused with a retryable 409. The browser sends its own elapsed recording time with the request, and the `capture_coverage` metric judges against it rather than against the span to the last upload, which also counted the time a stop spent uploading.
8. The web client shows a live capture or processing status workspace while the job runs.

If the user refreshes, closes, or navigates away from the Nojoin tab while recording (actual tab unload, not in-app navigation), the browser stops capture, drops the unfinished segment, and asks the backend to mark the recording `PAUSED`. Uploaded segments remain on the server and queued ones in IndexedDB. On the next app load, Nojoin blocks new capture behind a mandatory modal offering resume, stop-and-process, or discard. Stop does not require a live browser runtime, so a recording in this state always has a route to processing.

Switching focus to another browser tab, window, or application does not pause capture. Navigating between pages within the Nojoin app also does not pause capture. Only a real Nojoin tab unload (pagehide/beforeunload) invokes the guarded pause path.

### Chunk Registration

Every segment becomes a `recording_audio_chunks` row with an offset on the recording's timeline, and every 20 s window (5 s hop) becomes a `recording_audio_window_manifests` row that the live ASR lane tracks its progress on. Registering a segment used to rebuild all of them: list the upload directory, load every chunk row, recompute every offset, rebuild every window and upsert every manifest, all under `FOR UPDATE` on the `recordings` row. A segment's cost grew with the meeting, and the segment upload, live transcription and finalize all wrote the same row, so they queued behind it. On a 2 h 19 m meeting the task passed the 2 s capture cadence at about segment 3,000 and reached 8 s by segment 4,000; uploads fell 14 minutes behind and a stop spent 13 minutes sending them (issue #417).

`backend/utils/recording_audio_registration.py` now registers a segment in constant time. A per-recording cursor (`recording_audio_chunk_cursors`) records how far the chunks are placed. A chunk is placed once every sequence before it is placed or has failed for good (a `.transcode_failed` marker or a `.corrupt` tombstone). The full rebuild computes the same timeline: offsets are cumulative over the chunks present, and a missing sequence ends a contiguous group of windows. A segment that lands early waits, unplaced, until the gap fills. Placing chunks can only grow the last group, and a growing group can only change its last few windows, so registration rebuilds only the windows from the first one that was not yet full, and it writes only those that are new or whose geometry changed. A window it does not touch keeps its ASR and diarisation status exactly as they were. Measured on PostgreSQL, a segment costs 6.8 ms at segment 100 and 6.6 ms at segment 4,150, where the old full sync took 3.3 s per segment by segment 4,200; both produced identical windows.

The bookkeeping is serialised per recording by a transaction-scoped advisory lock (`pg_advisory_xact_lock`) rather than a lock on the recording row, so it never blocks a write to `recordings`. The segment upload writes `last_activity_at` at most every 15 seconds rather than on every segment.

Finalize keeps the full rebuild as its reconciliation pass and then seals the cursor. A sealed cursor turns registration into a no-op, which closes a race where a transcode task still queued at stop time ran the full sync afterwards with `seal_tail=False`, unsealing every window finalize had just sealed and resetting its progress. A resume after a failed stop drops a sealed cursor so registration rebuilds it from the chunk rows; the same rebuild serves a recording that was in flight across the upgrade and has no cursor yet.

When a recording is active, a floating recording badge appears at the top-centre of the viewport showing the recording status, elapsed time, and pause, resume, and stop controls. Clicking the badge navigates to the recording detail page. The badge remains visible on every page except the recording detail page so the user never loses visibility of the active recording while navigating the app.

## Processing Pipeline

The normal backend processing path is:

1. Validation.
2. VAD and audio preprocessing.
3. Proxy creation for web playback.
4. Transcription via a pluggable engine under [backend/processing/engines/](../backend/processing/engines/) (Whisper by default, Parakeet or Canary via onnx-asr selectable sharing `OnnxAsrEngine`). Whisper re-decodes a window by sampling when its greedy output fails a quality threshold; that sampling is seeded, so reprocessing a recording on the same image gives the same transcript. Parakeet and Canary transcribe in windows of 15 to 30 seconds that close at a pause, and skip a window with no signal (`_chunk_boundaries` in `onnx_asr_engine.py`). onnx-asr returns no token timings for Canary, so each Canary window is one segment and takes its dominant speaker in the merge. For Parakeet it returns each token's start only, so `map_onnx_asr_recognition` ends each word at the earliest of the next word's start, the window's end, the first silence that begins after the word starts, and its start plus the word length cap (`asr_word_length_cap_s`, default 0.4 s). The cap is at most single-word flip smoothing's default maximum word length (0.45 s), so a capped word is never too long to smooth at that default. A silence is a run of exact zeros at least 0.2 s long, which VAD writes over non-speech. Unmuted audio (the live lane's clips, or a final pass with VAD off) usually has none, so there the other three bounds decide. Invariant: no word ends after the first silence that begins after its start, because a word stretched across a pause can end inside the next speaker's turn and be matched to them. A word whose token is stamped inside a muted run is not bounded by that run.
5. Pyannote diarisation, optionally bounded by the recording's `max_speakers`.
6. Phantom speaker filtering.
7. Merge (see [Word Timestamps In The Merge](#word-timestamps-in-the-merge)), voiceprint extraction, and deterministic speaker resolution.
8. Canonical finalize, when `enable_canonical_transcript_writes` is on (the default): `finalize_utterances_from_segments` in [backend/utils/canonical_pipeline/core.py](../backend/utils/canonical_pipeline/core.py) writes the merged segments as finalized utterances and supersedes every other active utterance, the live lane's included, then the transcript projection is rebuilt from them. With the setting off, the merged segments stay in the transcript's JSON alone. Speaker boundaries come from the merge alone: it has already given each timestamped word the speaker whose turn contains it, and a segment without word timings keeps its dominant speaker whole. Invariant: finalize's canonical writes and the rebuilt projection are committed before meeting intelligence runs, so a failure in the later stages, which rolls the session back, cannot undo them.
9. Automatic meeting intelligence when an AI provider and model are configured.
10. Automatic application of inferred speaker names to unresolved speakers, plus persistence of the meeting title and Markdown meeting notes. Applied suggestions are retained on the transcript as an audit trail.

### Word Timestamps In The Merge

Word timestamps are optional per segment (an onnx-asr window without token timings, a Whisper segment with an empty `words` list). `combine_transcription_diarization` in [backend/utils/transcript_utils.py](../backend/utils/transcript_utils.py) chooses the method per run of consecutive segments: a run with words is aligned word by word, a run without gets each segment's dominant speaker over its span. Every segment's text reaches the merge output. A word no longer than pyannote's segment precision takes the speaker of the turn containing its instant (`[start, end)`; a turn's end counts when no turn starts there; a gap stays `UNKNOWN`).

Consolidation never drops text. A segment under 0.1 s folds into an adjacent segment at most 1.0 s away that stays within the 10 s maximum, preferring the same speaker, then the nearer one. The receiver keeps its speaker and overlapping speakers; ids and edit flags merge as in any consolidation merge. Each fold bridges at most 1.0 s, but folds into one segment add up. Fragments with no qualifying neighbour stay as separate short segments, and only a fragment without text is dropped. Neighbours whose fragments all folded away merge only if the original pieces tiled (every gap under 0.01 s) with the same speaker and overlapping speakers, within 10 s.

### Processing Tuning

Eight values inside these stages can be overridden: the VAD threshold (stage 2, and the live lane's VAD), the Parakeet word length cap (stage 4, both lanes), the four phantom filter thresholds (stage 6), and single-word flip smoothing (stage 7). Their keys, shipped defaults and bounds live in one place, [backend/processing/processing_tuning.py](../backend/processing/processing_tuning.py), which imports nothing heavy so the API validates against the same table. The processing modules take their default constants from it.

The values are per-user settings. A stage resolves each one as: an explicit function argument, else the recording owner's merged settings (their own value over the install's flat `config.json` key), else for the VAD threshold the legacy `vad_parameters.threshold`, else the shipped default. Unset is `None` and means inherit. A non-`None` default on the user row would be written onto every row by the settings page's whole-object autosave and would hide both the install value and any later change to a shipped default, so the defaults stay `None`.

The invariant: a tuning value never fails a recording. The API rejects out-of-range values, but a stored or `config.json` value can still be unusable (hand-edited, restored from a backup, which writes `user.settings` as archived, or written before a bound tightened). The worker logs such a value and uses the shipped default instead (for the VAD threshold, `vad_parameters.threshold`) rather than raising, and treats a phantom floor at or above the phantom merge threshold as both defaults. The API checks that rule on the same pair the worker reads, the user's values over the install's flat keys, so a pair it accepts is the pair processing uses, unless the install's values later change. GET returns the install's flat values the settings page needs for that check as a read-only `processing_tuning_install` map, keyed by tuning key. The owner's merged value is a single tier, so an unusable per-user value falls back past the install's flat key. With no value set anywhere, every stage uses the shipped defaults. New code that reads one of these values must go through `resolve_tuning` (or, for VAD, `get_vad_config_from_settings`) rather than reading the settings directly.

### Transcription Failure Versus Silence

A transcription engine's `transcribe()` either returns the canonical result or raises `TranscriptionError` ([backend/processing/engines/errors.py](../backend/processing/engines/errors.py)); it never returns `None`. A result with empty text and no segments is a success: the audio held no speech. The dispatcher `transcribe_audio` wraps anything else an engine lets escape, so `TranscriptionError` is the one exception callers treat as a lost transcription. The error's message is written for the UI and names a GPU out-of-memory failure as CUDA out of memory; `gpu_out_of_memory` flags it for code. Out-of-memory counts as a GPU failure only when the model ran on the GPU: an onnx-asr model none of whose sessions runs on the CUDA provider reports the server running out of memory instead (when its sessions cannot be inspected, a GPU host is assumed). After an out-of-memory error the engines drop every reference to the model, including the locals of the frames the error's traceback keeps alive, and collect it, so the memory is actually freed (onnxruntime's arena never shrinks); the live lane, which never retries, therefore does not reuse an exhausted session for every later region. Celery time limits and termination are not transcription failures: engines, the dispatcher and the final pass re-raise them unchanged.

Final processing retries a GPU out-of-memory failure once, after releasing every cached model (ASR, diarisation, embeddings) and the CUDA cache; if releasing them fails, the original out-of-memory error is reported. Any failure that remains sets the transcript's `transcript_status` to `error` with the message in `error_message`, marks the recording `ERROR` with the same message as its `processing_step`, and stops before diarisation, speaker assignment, meeting intelligence and post-processing follow-ups, none of which can produce anything from a transcript that does not exist. Provisional live text already on the row is kept. `Retry Speaker Inference` returns such a recording to `ERROR` rather than `PROCESSED`; only reprocessing can repair it.

`error_message` is shared with notes failures, so ownership follows `transcript_status`: while it is `error` the message is the transcription failure. Only the `Transcript` methods `fail_transcription`, `complete_transcription` and `set_notes_error_message` write it, so notes runs never clear or overwrite a transcription failure, and a backup restore that settles an interrupted recording marks its transcription failed the same way. Notes cannot be generated for a failed transcription: the API answers 409 and a notes task queued earlier does nothing. A later run that completes clears a transcription failure. When that run finds no speech at all it leaves `notes_status` alone, since there is nothing to summarise, which matches the success path skipping meeting intelligence for an empty transcript.

The invariant this protects: an empty `completed` transcript means the meeting was silent, never that ASR crashed. Code that calls an engine must not turn a `TranscriptionError` into an empty result. The live lane still records a failure as a failure but does not stop for one: it is best-effort and the final pass transcribes the whole recording again, so a region whose transcription fails is logged and dropped, and the lane keeps advancing.

### Speaker Cap And Voiceprint Versioning

`Recording.max_speakers` is an optional per-recording upper bound. `NULL` means auto-detect and is the default; that path passes no speaker keyword to pyannote at all. A set value is applied as pyannote's `max_speakers` and never as `num_speakers` — an exact count forces a split whenever the user overcounts, which is the over-clustering failure the field exists to prevent. It is settable at import, on the reprocess request, and throughout a live capture, since diarisation runs at stop time.

Voiceprints are only comparable with others produced by the same extraction procedure. `EMBEDDING_METHOD_VERSION` in [backend/processing/embedding_version.py](../backend/processing/embedding_version.py) records which one produced a stored vector, persisted on both `global_speakers.embedding_version` and `recording_speakers.embedding_version`. That module holds nothing but the version and its history, so a request path can reason about versions without importing torch and pyannote. Speaker identification and the duplicate-merge pass both refuse to score across versions; a cross-version cosine value resembles a similarity but is not one. Any change to how embeddings are produced must bump the version, and any new comparison site must check it — `embeddings_are_comparable` exists for that.

`DUPLICATE_SPEAKER_MERGE_THRESHOLD` stays at 0.70 deliberately. When the first extraction method put 29% of same-person pairs below it, the fault was the input to the comparison, not the threshold: same-person similarity collapsed whenever acoustic conditions changed, the very condition that makes the diariser split one person. Lowering the threshold causes wrong merges, which a user finds harder to undo than wrong splits.

The pass links clusters by complete linkage: a cluster, or a group already formed, joins another only when every pair across the two clears the threshold. Single linkage, where one qualifying pair was enough, let a small cluster of short backchannel lines bridge two people: its blended voiceprint scored 0.87 against one cluster and 0.74 against another, the two clusters scored 0.42 against each other, and the union gave 79% of a 75-minute board meeting to the wrong person. The direct score between two clusters is the evidence that they are one voice, and no chain through a third cluster overrules it. Replayed over this project's own library, the rule changed three recordings in a hundred, two of which had been bridged that way. The cost is a near miss that stays split, which one merge in the speaker panel fixes.

A group that carries a person's identity is also held to that person's library voiceprint. Before the linkage check, every cluster in the group a union would form is scored against the person's library voiceprint, except clusters already identified as that person, and the union is refused if any of them scores below `IDENTIFIED_MERGE_VOICEPRINT_FLOOR`. The floor is 0.60, the value `DRIFT_GUARD_THRESHOLD` uses to decide whether an observation may be blended into a library voiceprint. Clusters that score as one voice against each other are kept apart when one of them does not sound like the person, and the refused cluster keeps the `Speaker N` name it was given before the pass. A library voiceprint from another extraction version is not scored; that person's merges rest on the in-recording scores until the rebuild task repairs it. On the same library the guard changed one recording in 107. There a cluster holding the largest share of the meeting scored 0.74 against an identified speaker's cluster and 0.31 against that person's library voiceprint, and complete linkage alone would have merged the two. The weakest genuine second cluster the guard let through scored 0.65.

Stale voiceprints are repaired by `rebuild_voiceprints_task` rather than by comparing them anyway. The repair is automatic scheduled maintenance, running every six hours from Celery Beat and processing at most `AUTOMATIC_VOICEPRINT_REBUILD_LIMIT` (25) recordings per tick. It is bounded rather than operator-triggered because staleness is not a state a user can act on — it is a maintenance obligation Nojoin owes itself after changing its own extraction method — and because an unbounded re-extraction would run the embedding model over an entire library on hardware the user owns. Bounding each tick keeps the GPU lane responsive; a large library converges over several ticks. A stale voiceprint that cannot be re-extracted, because its audio is gone or its speaker owns no transcript segment, is cleared rather than kept: it could never be scored against anything, and leaving it would stall the sweep on a row it can never repair. Transient extraction failures are counted separately and leave the voiceprint in place so a later run can retry.

Three `pipeline_metric` stages make diarisation quality inspectable from a worker log. `final_diarization_speaker_stats` records per-cluster speech duration, segment count and share, plus overlapped speech and whether a cap bound. `speaker_identification` records, for each cluster, the three closest people in the library with their cosine scores and which one matched, so a miss says who came closest and an ambiguous pair is visible as one. `speaker_merge_pass` is emitted on **every** run of the duplicate-merge pass, including runs that merge nothing and runs that could not score anything (which carry an explicit reason); it lists each pair's cosine score, why a pair that cleared the threshold was still refused, each merged group with its survivor, its weakest and mean score and each absorbed cluster's score against the identified person's library voiceprint, and whether each identified person's library voiceprint could be consulted.

A user can discard a recording at any in-flight stage: uploading, paused, queued, or processing. Discard is a single graceful operation that revokes the running Celery task with `terminate=True`, deletes every on-disk artefact, and removes the recording row, so no manual cancel-then-delete sequence is required. Terminating the task stops the worker from continuing the pipeline, and the worker's start-of-task cancellation guard prevents a revoked-but-requeued task from resuming work. Terminal recordings (processed, errored, or already removed) are deleted through the standard delete flow instead.

Per-user language preferences are resolved once through the shared backend language registry. The effective transcription language is propagated to live, final, imported, and reprocessed ASR calls and included in ASR result hashes. Whisper receives an explicit language only when one is selected; Canary receives its supported source-language parameter; Parakeet remains multilingual auto-detection and therefore hashes as automatic language.

Generated-content language is independent from source-audio language. Manual notes generation, unified automatic meeting intelligence, standalone title generation, and secondary-provider fallback receive the same resolved output-language instruction. Prompt control text and JSON keys remain stable, while titles and Markdown content can be localized. The automatic intelligence contract accepts any non-empty top-level Markdown heading rather than requiring the English `# Meeting Notes` heading.

Manual user notes can be captured during recording or processing and are fed into both the automatic meeting-intelligence stage and the manual note-generation flow.

If AI configuration is missing, the recording still completes with transcript, diarisation, and deterministic speaker resolution intact. Automatic AI enhancement is skipped rather than failing the meeting. Manual `Generate Notes` and `Retry Speaker Inference` remain available once AI is configured.

A secondary LLM provider can be configured via the `SECONDARY_LLM_PROVIDER` environment variable. When set, all AI features (meeting intelligence, Meeting Edge, speaker inference, chat) automatically fall back to the secondary provider if the primary provider fails with any error, handled by `SecondaryLLMBackend`. The secondary provider has its own model, live model, and API key settings, configured independently. Fallback is transparent: the primary provider is tried first, and on failure the system logs a warning and retries with the secondary provider. If both fail, the primary provider's error is raised.

Notes generation, Meeting Edge and Meeting Chat read segments through `build_transcript_segments_for_read` (the canonical utterances, else the `Transcript.segments` projection) and name speakers with `build_recording_speaker_map`. The canonical read runs in a savepoint and a failure is logged, so the projection fallback leaves the caller's transaction usable. Chat renders through `render_transcript_for_llm` (`backend/utils/llm_transcript.py`): `[MM:SS]` start-time lines, without the unattributed `UNKNOWN` lines (such as a removed speaker's) the transcript view hides; notes generation still sends them. The pipeline's first-pass meeting intelligence and the MCP `get_transcript` tool format their own lines.

Meeting Chat sends the meeting's notes and full transcript with every turn, then adds excerpts from the search index (`_chat_retrieval_filters` in `routes_chat.py`). It retrieves attached documents from this meeting and from any meetings the user's tags bring in, and transcript excerpts from those other meetings only. The meeting's own transcript is left out of retrieval because the prompt already holds it, and the index is rebuilt only after processing, so its excerpts can predate an edit or a speaker rename. Chat answers with 409 while a recording is paused, uploading, queued or processing (`IN_FLIGHT_TRANSCRIPT_STATUSES` in `backend/models/recording.py`), the same states in which the recording view blanks the transcript.

Left at its defaults, Ollama does not refuse an oversized prompt: past `num_ctx` it drops tokens from the front of the prompt and answers from the rest (current releases keep about half the window), and when the answer fills the window it shifts the context and loses the start of the prompt the same way, each time ending in an ordinary stop. The hosted providers reject an oversized request outright. Every Ollama `/api/chat` request therefore lets Ollama decide whether it fits (`OllamaLLMBackend._post_chat`):

1. `num_ctx` is the configured window (`ollama_context_window`, the operator's VRAM budget), lowered to the model's trained context length from `/api/show` when that is smaller, because Ollama clamps to it at load. It is never raised automatically: a larger KV cache is exactly what fails to load on consumer GPUs. `/api/show` is cached per process for five minutes per server and model.
2. The request carries `truncate: false`, so Ollama refuses a prompt longer than `num_ctx` with HTTP 400 before generating (0.34.2 and 0.40.2 report the exact token count; 0.12.6 does not), and `shift: false`, so an answer that fills the window ends with `done_reason: length`. No `num_predict` is sent, so a Modelfile's own limit stands. The 400 becomes a context-window error naming the window and the count, and a length stop an "incomplete answer" error; neither answer is used.
3. Meeting chat drops the oldest turns itself, against an estimate of 2.6 UTF-8 bytes per token (prose and transcripts run 2.6 to 5; numbers and code run denser and go through the retry) with up to a quarter of the window kept for the answer; the last message, which carries the meeting context and the question, is never cut. If Ollama still refuses, chat retries once with fewer turns (by Ollama's count when given, otherwise with none), then reports the refusal.
4. Servers before Ollama 0.12.6 ignore `truncate` and `shift`. For them the token counts are checked after the call: a prompt plus answer past `num_ctx` (a cut prompt counts the whole window) is refused as truncated. Their answer cap stays Ollama's default of ten windows.

A refusal raises `ContextWindowExceededError` (`backend/utils/context_window.py`) naming the window, and the Ollama backend re-raises it as it is instead of wrapping it, so callers can tell it from every other failure. A configured secondary provider takes over as for any other primary failure; a streamed chat refused by Ollama falls back too, because the refusal arrives before the first token. A length stop at the end of a streamed answer, or a truncation found after the call on a pre-0.12.6 server, arrives after the text has streamed, so the answer is withdrawn (not saved) and there is no fallback. Speaker suggestions and document pages that do not fit report the refusal as their error. Notes and chat go on as [Meetings Longer Than The Context Window](#meetings-longer-than-the-context-window) describes.

To cut token cost on repeated context, Meeting Chat and Meeting Edge lay out their prompts cache-first — the large, stable portion leads and the volatile part is sent last. Meeting Chat sends the meeting notes and full transcript as the system prompt (the Anthropic backend marks it with a `cache_control` breakpoint; OpenAI-compatible providers reuse the leading system message through automatic prefix caching), leaving only the conversation history and the user's question in the messages array. Meeting Edge splits its single prompt into a stable instruction/JSON-schema prefix and the volatile per-refresh context (rolling summary, recent transcript), and the Anthropic backend `cache_control`-marks that prefix. Caching is transparent to the user — it changes only how the request is framed for reuse, not what the model is asked — and simply yields no benefit when a provider or model does not support it.

Playback, transcript viewing, and export all operate on the full recording timeline without applying persisted trim offsets.

#### Meetings Longer Than The Context Window

The recommended setup for long meetings is a hosted provider, or Ollama with a hosted secondary provider: the secondary receives the whole meeting after Ollama refuses it, as for any other primary failure, and answers in one call. Only when there is no secondary provider, or it fails too, does Ollama write the notes in parts. `SecondaryLLMBackend._call_with_secondary` re-raises the primary's refusal when both fail, so the worker sees the same `ContextWindowExceededError` with or without a secondary, and calls `generate_meeting_notes_in_parts` (Regenerate Notes) or `generate_meeting_intelligence_in_parts` (the automatic pipeline) with it. The wrapper sends those to the primary. The steps are in `backend/utils/long_meeting_notes.py`, with the splitter in `backend/utils/transcript_parts.py`:

1. The budget comes from the refusal. Ollama 0.34 and later report the prompt's token count, which gives this meeting's tokens per byte, plus a tenth for parts denser than the whole. Without a count, the 2.6 bytes per token estimate that chat trimming uses applies.
2. The transcript is split between lines, each one utterance, into the fewest parts that fit, and never fewer than two, each about an equal share. An utterance longer than a part is cut between sentences and every piece keeps its timestamp and speaker. Each part's room is the window less a quarter for the draft's answer (at most 8,192 tokens) and less the rest of the prompt. If that leaves under a quarter of the window, the run is refused with a message that names the attached documents, since they are the part of the prompt that grows without limit.
3. Each part is drafted with the same prompt prefix (instructions, speaker labels, metadata, glossary, user notes, calendar context, attached documents and the notes structure), followed by the part's number, its times and its transcript, so a server that keeps its prompt cache between requests reads the prefix once. A draft Ollama refuses, or whose answer runs out of window, is halved and both halves are drafted.
4. The drafts are combined in one call that keeps a third of the window (at most 16,384 tokens) for the answer and leaves the attached documents out, because the drafts already drew on them. Combining calls hold notes rather than transcript, so they are priced as prose at 2.5 bytes a token. Every draft, and every combining call short of the last, is given a word count that lets about three share a call, so each round of combining leaves fewer, no longer notes than it started with; the last is given a word count that fits its answer room. Drafts too long for one call are combined in consecutive groups first, and the groups again, until one call can take them all. A combining call Ollama refuses, or whose answer runs out of window, is split in halves. Where the price keeps every pair apart, they are tried in pairs anyway and Ollama decides; two drafts Ollama will not combine end the run with an error. Each call is asked once more after an Ollama server error, so one failed request does not cost the parts already written.
5. In the automatic pipeline, each draft also names the unresolved speakers its own part gives evidence for, in a `<speaker_names>` block after its notes, and leaves unresolved labels as they are in the notes themselves. Every call in parts answers in Markdown with such tagged lines rather than in JSON: over dozens of calls a 7B model held to `format: json` loops on one token often enough for Ollama to abort the answer ("token repeat limit reached"), and without the constraint it often stops writing JSON at all. A speaker who introduced themselves is named by the introduction, found with the same rules as a run without AI (`detect_rule_based_speaker_suggestions`), whatever the drafts said: on a 133-minute test meeting, qwen2.5:7b drafts of 33-minute parts attached two introduced names to the wrong labels. For the other labels, one every part named alike keeps that name, where one name containing the other counts as alike and the fuller name is kept; placeholders such as "Speaker 2", and the prompt's own example copied back, are ignored. For a label the parts named differently, the final call is given the names they suggested and picks one, preferring a name the meeting gave over a role, or none. Only an answer that is exactly one name on the label's list counts, so the final call cannot invent a name, and copying the list back is no choice: the names are applied to speakers without review, so a wrong one costs more than none. The final call also writes the title on a `<title>` line, and the names are put into the notes afterwards, as `apply_speaker_mapping_to_notes` does for a single call, so two parts that disagree about a speaker cannot leave two names in the notes. An answer that cannot be read is asked for once more.

A meeting that fits keeps the single call and a byte-identical prompt.

Meeting Chat follows the same order. When every configured provider has refused the whole transcript before any text streamed, the chat endpoint asks once more with the passages of this meeting most relevant to the question. `_transcript_excerpts` in `routes_chat.py` searches this meeting's transcript chunks in the index with the question's embedding and keeps only their times. The backend reads the lines at those times from the canonical transcript (`render_transcript_excerpts_for_llm`), so an edit or a rename made since indexing still shows, takes them most relevant first up to the room the window leaves, and shows them in time order with `[...]` at each gap. On Ollama that room is the window less a quarter for the answer (at most 2,048 tokens), the notes and the question, and up to an eighth of the window for recent turns. The prompt says the notes cover the whole meeting and the excerpts are not the whole transcript. Without a query embedding, or with no index for the meeting yet, chat answers from the notes alone and the prompt says so.

### Imported Media Input

Import stores no video.

- **Worker step.** Every import route (`/import`, chunked finalize, `/upload`) stores the upload as it arrived, marks the recording QUEUED with the step "Extracting audio..." and queues `keep_imported_audio_task` on the cpu lane in place of processing ([backend/worker/tasks/imported_audio.py](../backend/worker/tasks/imported_audio.py)). The task runs `keep_imported_audio`, points `audio_path` at the result, rebuilds the import's audio window from it, deletes the upload once that write has committed (a failed deletion is logged as an error), then queues processing and the playback proxy.
- **Duplicates.** Each write is conditional on the recording still waiting for the task with the same `audio_path`, and the upload survives until the kept audio is stored, so a duplicate or re-queued copy is harmless: the copy whose write lands keeps its audio, and every other copy removes only what it extracted. A fault before that write leaves the upload and the waiting recording for the next copy. A task is acknowledged on receipt, so one lost with its worker is not redelivered: the cpu worker re-queues every waiting import when it starts (`requeue_imports_waiting_for_audio`); the gpu worker's startup sweep leaves them to it. An ffmpeg whose worker process was killed, by a crash or by the `terminate=True` revoke in discard and permanent delete, runs on to the end, but its file is in the recording's `temp/<id>/`, which deleting the recording removes and the daily sweep clears. Delete, permanent delete, batch permanent delete and discard read the row `FOR UPDATE`, so they never delete a path the task has just replaced; deleting a user does not lock its recordings.
- **Extraction.** `keep_imported_audio` ([backend/utils/import_audio.py](../backend/utils/import_audio.py)) extracts the audio track ffmpeg selects by default from any media container, or any upload carrying video, into a new file in the recording's `temp/<id>/`. The task moves that file beside the upload only after its write has matched the waiting row, so a delete or discard either removes it with the temp directory or waits for the commit and removes it by its stored path. The upload is left for the task to delete. Other uploads are kept as they arrived. WAV, MP3, AAC and FLAC, whose formats hold no video (cover art aside), are kept without being probed, so they need no ffmpeg.
- **Format.** AAC, ALAC, MP3, Opus, Vorbis and FLAC are copied, PCM and other lossless codecs become FLAC, and anything else becomes Opus.
- **Verification.** The new file is kept only if it is one audio stream no more than max(1 s, 0.1%) shorter than the source track, measured from packets.
- **Failures.** A file at fault (no audio track, or one ffmpeg cannot extract) and a server fault (a tool missing, timed out or stopped by a signal; a full disk) both mark the recording ERROR, with the reason as its step, and remove its files, the upload included; a failed deletion is logged. Neither is retried, and reprocess refuses a failed recording with no audio left, answering with that reason.

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
   suppress in-flight transcript text and segments, so provisional text never
   reaches the sidebar or exports; the utterances
   endpoint is the only in-flight transcript surface.
4. Live utterances carry no speaker. Every provisional utterance is written
   with the `UNKNOWN` label, because diarization runs only at finalize; speaker
   identity for live text is therefore resolved by the final pipeline's
   diarisation pass rather than during capture. Transcript edits made by the
   user are treated as authoritative.
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
ASR lane records whether the live lane consumed the window audio; rows from
older releases can also read `catch_up_processed`. Nothing writes the
diarisation lane since rolling and catch-up diarisation were removed: new
windows start `pending` and stay there, and older rows keep what those passes
recorded. The legacy window `status` field remains a compatibility projection;
new logic should inspect the lane-specific ASR and diarisation fields.

A run that fails outright still accounts for the audio it drained. If it had
already carried the audio past its cut point into `live/buffer.wav`, that buffer
is kept and `buffer_abs_start` moves to where it starts, exactly as a successful
run would leave it. Otherwise the run's audio is spent: `buffer_abs_start` moves
past the audio the run read, and the buffer and the `live/context.wav` run-up are
removed. When the run failed while reading, its length comes from the WAV
headers, falling back to the buffer length saved in `live/state.json` at
carry-over (`buffer_len_s`) and to the chunk durations recorded at upload.
Advancing only `next_expected` would replay the stale buffer in the next run and
stamp every later utterance early by the length of the failed run. The new state
is saved in one write before any audio is removed, so a failed write leaves the
buffer consistent with the state on disk, and `next_expected` advances even when
the timeline cannot be worked out.

The live lane is best-effort: any failure is logged, the lane still advances,
and nothing is re-raised. When the recording finalises, `process_recording_task`
transcribes the whole recording, diarises it when diarisation is on, and writes
the result over the live utterances, keeping the user's text and speaker edits
on the lines it matches. A different transcription engine is reserved for
explicit manual reprocessing after an administrator changes the install's
transcription engine in Settings.

Final processing may reuse live transcript text and source-channel speaker
authority only after a stable utterance id match or a clear one-to-one time
overlap match. It must not align live and final segments by array index. When a
merged, split, or low-confidence span is ambiguous, final processing keeps the
final ASR/diarisation output and records live evidence in alignment metadata
instead of silently applying it to the wrong time span. Manual text and speaker
locks remain authoritative.

A speaker correction (a rename across the recording or from an utterance
forward, a merge, a global-speaker link or promotion) changes only what it
names: the utterances its scope selects, or the speaker's identity. It never
re-derives other utterances from stored diarisation window rows
(`diarization_window_results`, `diarization_window_turns`). Live captures
finalised by v1.0.0 to v1.2.0 can still hold window turns from the removed
rolling diarisation, and replaying them moved other speakers' lines onto the
corrected speaker.

Every finalised utterance also keeps the cluster label the diariser gave its
span (`transcript_utterances.diarization_label`), stamped on the segments
before speaker assignment and the merge pass rewrite `speaker`. It is what
makes an automatic merge reversible: `unmerge_recording_speaker` restores a
merged row as an unnamed speaker, gives it back every line that carries its
label except lines a person assigned by hand, and records an
`unmerge_speakers` correction event. The speaker panel lists such clusters
under the surviving speaker. Lines written before the column existed carry no
label, so a merge made before then can only be undone by reprocessing.

### Utterance Public Ids

`transcript_utterances.public_id` is unique across every recording. A segment's
`id` is only a request: it carries live utterance ids into finalize and a
recording's stored ids into backfill, but openai-whisper numbers its segments
in the same field. The merge keeps only string ids, so those numbers reach
finalize only through the fallback that skips the merge (diarisation off or no
merged segments), and backfill through older stored projections. Finalize and backfill (`replace_utterances_from_segments`) take
ids from `UtterancePublicIds` in
[backend/utils/canonical_pipeline/public_ids.py](../backend/utils/canonical_pipeline/public_ids.py).
An id that no utterance holds is kept (finalize also requires a canonical UUID);
one this recording holds goes into the new row's
`confidence_payload.source_public_ids` and is replaced by a fresh uuid4;
anything else gets a uuid4. The bulk segment edit's full-replace fallback keeps
the client's ids and answers 409 when one is held or repeated. The live append
persists the live lane's uuid5 of recording id, span, speaker and text.

### Startup Canonical Cutover

Older meetings are brought into the unified pipeline by a container-level
startup cutover.

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

A live recording is the one workload where a stall is unrecoverable. The browser keeps counting wall-clock time while nothing reaches the server, and audio for that stretch is lost.

The API therefore runs a watchdog (`backend/utils/stall_watchdog.py`) on its event loop, reporting two things:

- **Event-loop lag**, measured as the overshoot on a fixed sleep. Whatever froze the process shows up here, because the sleep cannot return on time: a blocked event loop, a throttled cgroup, and a host paging the process out are indistinguishable from inside and all detected. Reported as an `event_loop_stalled` pipeline metric when the overshoot exceeds the threshold.
- **Pressure Stall Information**, read from `/proc/pressure` at the moment lag is detected. This is what names the cause, since PSI's `avg10` window still reflects a stall that has just ended. Absent on non-Linux hosts and PSI-less kernels, where the lag signal survives without attribution. Sustained high pressure without a stall is reported separately as `host_pressure_high`, rate-limited so a persistently degraded machine does not bury the moment it started.

Nothing is sampled into the log on a schedule; a quiet system stays quiet. Tuning and switches are listed in [DEPLOYMENT.md](DEPLOYMENT.md#stall-detection).

## Anonymous Telemetry

Two Celery Beat tasks on the IO lane report to the collector, both identified only by a random install id held in `data/.install_id`, and neither carrying meeting content, names, or addresses.

- **The install heartbeat** carries the install id and the version. A task checks every fifteen minutes and sends when the install has never sent one, has started on a new version, or has not sent one this UTC day. It needs no consent; `NOJOIN_TELEMETRY_ENABLED=false` and an opt-out recorded before the heartbeat existed are its only exemptions.
- **The usage diagnostics** describe deployment scale, configuration shape, and feature adoption, every six hours once the install consents. The receiving service upserts on install id and UTC day, so the cadence buys resilience against a restart re-anchoring the beat interval rather than extra rows.

State ownership is split deliberately so no two processes write the same value: the API is the only writer of the `telemetry_*` keys in `config.json`, the worker is the only writer of the Redis send markers, and the install id file is write-once. The worker reloads configuration before every consent check, so an operator's opt-out takes effect on the next cycle rather than on the next container restart. Sending is best-effort: the diagnostics never retry, and a failed heartbeat waits an hour, so a network fault can never escalate into repeated calls. The collector keeps every row indefinitely.

The receiving Cloudflare Worker and its D1 schema live in [telemetry/](../telemetry/). See [TELEMETRY.md](TELEMETRY.md).

## Calendar Flow

1. An admin configures Google and/or Microsoft OAuth credentials for the installation.
2. End users connect their own accounts from **Settings > Integrations**.
3. Nojoin syncs selected calendars into stored dashboard-facing event data, including each event's description and attendee list.
4. The dashboard renders month markers, agenda items, next-event summaries, and colour-coded sources, combining synced calendar events with unlinked Nojoin recordings.
5. Recordings carry a nullable `calendar_event_id`; a recording is auto-linked to a confidently overlapping calendar event during processing (or linked manually), and the linked event enriches notes and speaker prompts while suppressing the recording's standalone dashboard calendar item.
6. Sync runs incrementally on the worker's embedded Celery Beat scheduler: every connected account with a selected calendar refreshes on a 15-minute cadence using each provider's change cursor (Google `syncToken`, Microsoft Graph `delta`). When an admin enables live sync and the instance is publicly reachable over HTTPS, Nojoin also registers Google `events.watch` channels and Microsoft Graph subscriptions, so changes arrive by webhook and enqueue an immediate incremental sync; the 15-minute schedule remains the always-on fallback.
7. A sync that cannot go incremental (the monthly shift of the 25-month sync window, a missing or reset cursor, or the Microsoft partial-occurrence repair) fetches the whole window and reconciles it by `provider_event_id`, updating stored events in place so `recordings.calendar_event_id`, which is `ON DELETE SET NULL`, keeps pointing at them. Events the listing omits are deleted, except those that ended before the window and that a recording links to.

## Authentication Model

Nojoin uses different auth shapes for different clients:

- **Browser traffic**: Secure HttpOnly session cookies. State-changing browser requests authenticated by that session must originate from the trusted Nojoin web origin, using standard `Origin` or `Referer` validation rather than relying only on `SameSite` and CORS.
- **Non-browser API clients**: Explicit bearer tokens.
- **MCP connector clients**: OAuth 2.1 bearer tokens (token type `mcp`) minted by Nojoin's built-in authorization server. These tokens authenticate only the `/mcp` endpoint, never the general API, and are contained by the same `token_version` and denylist machinery as sessions. Read tools need the `mcp:read` scope; write tools need `mcp:write` and make only recoverable changes. See [MCP.md](MCP.md).
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

The pipeline is gated rather than direct, and the ordering is the point. The images are built on the maintainer's release host by `scripts/release.py`, from a clean worktree of a commit whose CI is green since the previous release. Before anything is pushed, Trivy scans each image and fails the release on fixable CRITICAL or HIGH findings, with accepted exceptions recorded in `.github/trivyignore`, and a health-and-non-root smoke boots the API and frontend. Only then are the immutable `version` and commit-`sha` tags pushed, each carrying a build-provenance attestation and an SBOM, and the release tag after them. The release workflow checks each image was built from the tag's commit, cosign-signs it by digest, and only then advances the rolling `major.minor` and `latest` tags.

The consequence operators depend on is that a failing build can never advance the tag they pull by default. A briefly-exposed `vX.Y.Z` tag can exist when signing fails after the push, but it is visibly attached to a failed workflow run and is never reachable through `latest`. Building off GitHub did not move the trust anchor: signing is still keyless via GitHub OIDC, which binds each signature to the release workflow's identity and keeps a long-lived private key out of the threat model entirely. The release host does hold a registry token that can push images, and the workflow's revision check is what stops an image pushed for one commit being signed for another. Third-party actions are pinned to commit SHAs and base images, the release builder included, to `@sha256:` digests, so a mutable upstream tag cannot be repointed at unreviewed code after review. Anyone editing CI, the release workflow, the release script, or the Dockerfiles must preserve that pinning, the gate ordering, and the signing identity.

## Related Docs

- [CAPTURE.md](CAPTURE.md)
- [GETTING_STARTED.md](GETTING_STARTED.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [USAGE.md](USAGE.md)
- [CALENDAR.md](CALENDAR.md)
- [TELEMETRY.md](TELEMETRY.md)
- [DESIGN.md](DESIGN.md)
