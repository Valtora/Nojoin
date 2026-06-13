# Nojoin User Guide

This guide covers day-to-day use after Nojoin has been deployed and your account has been created.

For deployment, administration, calendar provider setup, backup operations, and detailed capture troubleshooting, use the dedicated guides in the `docs` folder.

## First Run

1. Open Nojoin in Chrome on Windows, Linux, or macOS for shared-audio recording, another supported desktop Chromium browser, or Chrome on Android/iOS for microphone-only recording.
2. Sign in with your account.
3. Open **Settings > Capture** if you need to choose a microphone or adjust shared-audio and microphone gain.
4. Use the **Meet Now** card on the dashboard and click **Start Meeting** to create a short test recording.
5. In the browser share picker, choose the meeting tab, window, or screen and enable the browser's audio-sharing or system-audio option when it is offered.
6. Speak briefly and confirm the live waveform responds. If AI is configured, Meeting Edge guidance may appear once enough speech accumulates.
7. Stop the recording, open it in the `/recordings` workspace, and wait for processing to complete.

Firefox, Safari, and mobile browsers other than Chrome can review recordings but cannot start live capture. Chrome on macOS is supported for recording; other Chromium-family browsers on macOS are best-effort. See [CAPTURE.md](CAPTURE.md) for the full support matrix.

## Tours And Onboarding

Nojoin includes guided tours for first-time users.

- The dashboard tour introduces navigation, recording, importing, capture setup, and settings.
- The transcript tour introduces the recording detail view when a recording is opened for the first time.
- Tours can be restarted later from the Help settings area.

## Dashboard

The root route is the operational home surface for Nojoin.

It brings together:

- **Meet Now** card for live browser capture.
- **Calendar context** through month and agenda views, including recorded meeting history.
- **Task List** for quick personal follow-up capture.

On desktop viewports around `1920x1080` and smaller, Nojoin automatically shifts into a denser desktop layout so more dashboard, recordings, transcript, notes, and settings content remains visible without affecting the roomier large-monitor layout.

### Calendar Surface

- Switch between month and agenda views.
- Use **Today** to jump back to the current date context.
- In month view, selecting a day opens a day agenda, and selecting today shows a live now marker against timed events.
- Unlinked Nojoin recordings appear on the dashboard calendar as orange meeting items, while Google or Microsoft calendar sources keep their own colours.
- Recorded meeting cards surface tags, speakers, and timestamps directly inside the selected-day agenda.
- The agenda view is month-scoped and includes both synced calendar events and unlinked Nojoin meeting history for the viewed month.
- Event times render in your configured Nojoin timezone.

Read [CALENDAR.md](CALENDAR.md) for connection and setup details.

### Task List

The Task List is a personal dashboard list for quick follow-up work.

You can create, rename, complete, reopen, archive, delete, and schedule tasks. Active deadlines show a live time-remaining badge. Archived tasks disappear from the dashboard immediately, including tasks that were already completed.

## Tasks Workspace

The dedicated **Tasks** page sits in the main navigation between **Dashboard** and **People**.

Use it to manage tasks more holistically:

- Select **Create Task** to open the task creation form when you need to add a richer task.
- **Open** shows active, non-archived tasks.
- **Completed** shows finished, non-archived tasks that can still be reopened.
- **Archived** shows hidden tasks and lets you restore them to the active task surfaces.
- Task cards support a title, body, deadline, permanent delete, direct recording links, and the same recording tag taxonomy used elsewhere in Nojoin.

Delete remains permanent. Use archive when you want to hide a task without losing it.

## People Workspace

The dedicated **People** page sits in the main navigation between **Tasks** and **Recordings**.

Use it to manage your shared speaker library:

- Review people records and stored voiceprints in one place.
- Search and filter the library when you need to find a known speaker quickly.
- Organize people with reusable people tags.
- Open batch editing and cleanup flows for broader speaker-library maintenance.

## Live Recording

Live recording is browser-native. On supported desktop Chromium browsers, Nojoin captures shared tab/window/screen audio plus microphone audio from the web app. On Chrome for Android and iOS, Nojoin records the phone microphone only.

1. Open the dashboard.
2. In the **Meet Now** card, click **Start Meeting**.
3. On desktop, select a meeting tab, application window, or entire screen in the browser share picker.
4. On desktop, enable the browser's audio-sharing or system-audio option when it is offered before selecting **Share**.
5. Allow microphone access if prompted.
6. On mobile Chrome, keep the phone close enough for the microphone to hear the meeting audio.
7. Keep the Nojoin tab open and the device awake until the meeting ends.

Tab sharing is usually the best choice for browser-based meetings because it most reliably exposes meeting audio. Window and screen sharing can work, but audio availability depends on browser and operating-system support.

If you close the browser share picker with **Cancel**, Nojoin silently returns to the pre-start state and no recording begins.

Mobile Chrome does not capture meeting tab, app, headset, or system audio. It is useful for microphone or in-room speakerphone capture only.

While recording, Nojoin shows recording state, duration, upload state, a live waveform, Meeting Edge guidance, your live notes panel, and collapsed processing visibility.

On mobile and narrow tablet layouts, Nojoin uses compact navigation with a menu button on the main dashboard surfaces. The active recording workspace and processed recording detail view both provide a native back control so you can return to the recordings list without relying on the browser's history buttons. Mobile Meeting Chat also includes its own back action to return to the meeting workspace.

You can switch to another browser tab, window, or application while recording. Nojoin only pauses automatically when the Nojoin tab is refreshed, closed, or navigated away from the active recording.

While a recording is active, a floating badge appears at the top-center of the viewport on every page. The badge shows the recording status, elapsed time, and pause, resume, and stop controls. Clicking the badge navigates to the recording detail page. You can control the recording from any page without navigating back to the recording workspace first.

### Pause, Resume, Stop, And Cancel

- **Pause** temporarily stops capture while preserving uploaded segments.
- **Resume** opens the browser share picker again and continues the same recording.
- **Stop** finalises the recording and starts processing.
- **Cancel** discards an in-progress recording.

If the browser is closed, refreshed, or loses the active recording page during capture, Nojoin pauses the recording to protect already uploaded data. When you return, Nojoin requires you to resume or discard that recording before starting anything else.

Paused recordings are retained indefinitely until you resume or discard them.

### Live Transcription

Nojoin still runs a live transcription lane during capture, but the recording page no longer shows provisional live transcript text while a meeting is in flight.

That live lane now works in the background to support Meeting Edge and to speed up later processing. The user-facing transcript appears after the recording has been finalized and the authoritative processing pass has produced review-ready output.

### Meeting Edge

Meeting Edge uses the recent live transcript window, an internally maintained rolling summary of the meeting so far (decisions, open threads, and action items), its own previous suggestions (so guidance stays fresh instead of repeating), your optional focus text, your manual notes, and linked calendar context when available.

It can surface live questions, missed points, and quick concept help during a meeting. In **Settings > AI**, you can optionally choose a separate Meeting Edge model for the current provider and tune the **Meeting Edge Technical Context** slider to make concept explanations stricter or more detailed. If the model field is empty, Nojoin reuses your main AI model.

## Importing Recordings

You can import existing audio files directly through the web client.

Supported formats include WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA, and OPUS.

The import flow validates the file, builds the canonical media artifacts, and queues background processing. Imports skip the live capture workflow but share the same final processing pipeline as live recordings.

### Cancel Processing

Use **Cancel Processing** from the recordings list or recording actions when a meeting is still uploading, queued, or processing.

Cancel Processing:

- Stops backend processing for that recording.
- Closes any active live upload or finalisation session for that meeting.
- Leaves the recording marked `Cancelled` instead of silently requeueing more work.

### Retry Processing

If a recording fails or you want to rebuild the generated meeting artifacts, use **Retry Processing**.

Retry Processing clears transcript-derived generated state, preserves recording metadata, tags, uploaded documents, and user-authored notes, then records a fresh processing timing sample for future ETA calculations.

### Reprocess A Recording

From the recording detail page you can choose **Reprocess at higher quality**. This re-runs the full pipeline after you change the transcription engine or model in Settings.

Reprocessing clears and rebuilds transcript and generated artifacts while preserving metadata, tags, documents, and user-authored notes. Older meetings that predate the unified pipeline cutover may require reprocess before transcript or speaker edits are available.

## Transcript And Playback

Within a processed recording you can:

- Play the aligned web proxy.
- Follow synced transcript highlighting.
- Click transcript text to seek playback.
- Edit transcript text and speaker assignments.
- Export transcript-only, notes-only, or combined text output.

Historical recordings carried forward from before the unified pipeline cutover may open in a read-only compatibility state. Playback, transcript viewing, and export remain available, but transcript or speaker edits require explicit reprocess first.

## Speaker Management

Nojoin maintains a global speaker library across recordings.

Common workflows include linking an unknown in-recording speaker to an existing global speaker, promoting a recording speaker into the People library, creating or updating voiceprints, recalibrating voiceprints from better samples, and merging duplicate speakers.

Voiceprint-backed speaker suggestions can appear during final processing and manual retry flows. Manual speaker edits are authoritative.

## Notes, Chat, Documents, And Search

Processed recordings can include Markdown notes, AI-generated meeting notes, meeting chat, uploaded documents, and transcript/document search.

- **Notes** are stored with the recording and can be edited after processing.
- **Generate Notes** runs a notes-only AI pass when AI is configured.
- **Meeting Chat** answers questions from the transcript, notes, and linked documents.
- **Documents** can be uploaded to support meeting context and later search.
- **Search** spans recordings, transcript text, notes, tags, and document content where available.

## Calendar Features

Nojoin can connect to Google Calendar and Microsoft Calendar when an administrator has configured provider credentials.

Calendar events can provide meeting context, dashboard agenda views, linked recording history, and Meeting Edge context. Read [CALENDAR.md](CALENDAR.md) before changing calendar provider settings or troubleshooting OAuth.

## Settings

Settings are grouped by task.

- **Profile**: account details and password changes.
- **Capture**: microphone selection, shared-audio gain, and microphone gain for browser recording.
- **AI**: provider configuration, model choices, automatic meeting intelligence, Meeting Edge model selection, and secondary LLM provider fallback.
- **Transcription**: transcription backend and model choices.
- **Calendar**: user calendar connections and timezone behavior.
- **Help**: tours and support surfaces.
- **Admin**: user, system, provider, release, and maintenance settings for administrators.

### Secondary LLM Provider

Nojoin supports configuring a secondary LLM provider as a fallback. When the primary provider fails with any error, the system automatically retries the request with the secondary provider. This applies to all AI features: Meeting Edge, meeting intelligence, speaker inference, and meeting chat.

The secondary provider has its own independent configuration:

- Provider selection (Gemini, OpenAI, Anthropic, or Ollama).
- Model and live model choices.
- API key or Ollama URL.

Configure the secondary provider through environment variables prefixed with `SECONDARY_` (e.g., `SECONDARY_LLM_PROVIDER`, `SECONDARY_GEMINI_API_KEY`). Leave `SECONDARY_LLM_PROVIDER` empty to disable fallback. The secondary provider configuration is visible in **Settings > AI** for administrators.

## Troubleshooting

- If live capture is unavailable, switch to Chrome on desktop for shared-audio recording or Chrome on Android/iOS for microphone-only recording.
- If remote participants are missing, start again and enable shared audio in the browser picker.
- If the microphone is missing, grant microphone permission and check **Settings > Capture**.
- If Nojoin reports a paused recording, resume or discard it before starting another capture.
- If processing fails, use **Retry Processing** or check the administrator logs.
- If calendar sync fails, review provider setup in [CALENDAR.md](CALENDAR.md).

## Updates

The Updates area shows the installed server version, latest available release, and release notes. Release metadata comes from GitHub Releases when the deployment can reach GitHub.
