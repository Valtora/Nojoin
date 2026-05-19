# Nojoin User Guide

This guide covers normal day-to-day use after Nojoin has already been installed.

For deployment, administration, calendar provider setup, and backup operations, use the dedicated guides in the `docs` folder.
For detailed Companion install, pairing, reconnect, switching deployments, tray usage, and troubleshooting, use [COMPANION.md](COMPANION.md).

## First Run

1. If the Companion is not installed or paired yet, follow [COMPANION.md](COMPANION.md).
2. Open the web app and confirm the Companion shows as connected.
3. Use **Meet Now** from the dashboard to create a short test recording.
4. Open the finished recording in the `/recordings` workspace.
5. Wait for processing to complete so the transcript and notes appear.

## Companion Connectivity and Pairing

The dedicated [COMPANION.md](COMPANION.md) guide is the canonical reference for Companion setup and recovery.

The short version:

- The Companion pairs to one Nojoin deployment at a time.
- Start pairing from `Settings -> Companion` in the browser by choosing `Pair This Device`.
- Pairing completes only after you approve the OS-native prompt opened by the local Companion app on the same machine.
- Start a fresh browser pairing request when you are replacing an existing backend pairing. The current backend stays active until the new pairing succeeds.
- `Temporarily disconnected` means the pairing is still valid and should recover automatically.
- `Local browser connection recovering` usually settles on its own. `Local browser connection unavailable` means you should relaunch the Companion and retry the browser action.
- No pairing code fallback exists. The browser request is signed by the backend and must be explicitly approved on this device.

## Tours and Onboarding

Nojoin includes guided tours for first-time users.

- The dashboard tour introduces navigation, recording, importing, companion setup, and settings.
- The transcript tour introduces the recording detail view when a recording is opened for the first time.
- These tours can be restarted later from the Help settings area.

## Dashboard

The root route is the operational home surface for Nojoin.

It brings together:

- **Meet Now** for live capture.
- **Calendar context** through month and agenda views, including recorded meeting history.
- **Task List** for personal follow-up work.

### Calendar Surface

- Switch between month and agenda views.
- Use `Today` to jump back to the current date context.
- In month view, selecting a day opens a day agenda, and selecting today shows a live now marker against timed events.
- Unlinked Nojoin recordings appear on the dashboard calendar as orange meeting items, while Google or Microsoft calendar sources keep their own colours.
- Recorded meeting cards surface tags, speakers, and timestamps directly inside the selected-day agenda.
- The agenda view is month-scoped and includes both synced calendar events and unlinked Nojoin meeting history for the viewed month.
- When calendars are connected, per-calendar colours help distinguish event sources.
- Event times render in your configured Nojoin timezone.
- If no calendar is connected and there are no unlinked recordings in view, the dashboard shows an empty state instead of fake data.

Read [CALENDAR.md](CALENDAR.md) for connection and setup details.

### Task List

The Task List is a personal dashboard list for follow-up work.

You can:

- Create a task inline.
- Rename a task by editing the title.
- Mark a task complete or reopen it.
- Delete a task.
- Set an optional date-and-time deadline.
- See a live time-remaining badge for active deadlines.

## Live Recording and Capture Workspace

When a meeting is actively recording, the recording page becomes a live capture workspace.

You can monitor:

- A unified live audio activity waveform.
- Recording state and duration.
- A Meeting Edge card with live questions, missed points, and quick concept help.
- A persistent notes panel for user-authored context.

If live audio stays quiet for a while, Nojoin may show a low-key inline reminder near the waveform instead of a persistent fault-style warning. `Dismiss` hides that reminder for the rest of the current meeting, `Don't show again` suppresses it until you re-enable it, and `Settings > Companion > Devices and alerts` lets you reset the warning state later.

Manual notes are captured with low-latency autosave behaviour so typing remains responsive while the meeting is live.

Meeting Edge also includes a smaller guidance field where you can tell the assistant what to optimize for during the meeting, such as timeline risk, unanswered dependencies, or decision ownership. It autosaves while you type and updates the live guidance after enough transcript signal is available.

### Live Transcription

While a meeting is recording, the live transcript pane is visible immediately. It shows a listening state until speech is detected, then provisional transcript segments appear as live utterances complete. Long continuous speech is force-emitted after roughly 8 seconds, so natural monologues do not wait for a 30-second cutoff. These segments use per-speaker colours and can be edited while the recording is still in flight. Live speaker names and live transcript edits are treated as authoritative and carried into final processing.

Live speaker labels are assigned by an online embedding matcher. Matching voice regions keep the same provisional `LIVE_XX` speaker identity, while very short or embedding-less regions reuse the most recent stable live speaker instead of creating a new speaker for every fragment.

The normal stop-to-final workflow uses the same transcription engine for live and final transcription so Nojoin can reuse the live transcript rather than transcribing the meeting again from scratch. Final processing still performs diarisation, speaker reconciliation, voiceprint work when enabled, and meeting-intelligence generation.

Meeting Edge uses the recent live transcript window, the latest saved live guidance summary, your optional Meeting Edge focus text, your manual notes, and linked calendar context when available. In Settings > AI you can optionally choose a separate Meeting Edge model for the current provider; if you leave that field empty, Nojoin reuses your main AI model.

## Importing Recordings

You can import existing audio files directly through the web client.

Supported formats include:

- WAV
- MP3
- M4A
- AAC
- WebM
- OGG
- FLAC
- MP4
- WMA
- Opus

Imported files enter the same processing pipeline as live recordings.

## Processing, Transcripts, and Retry Processing

Once uploaded, recordings are processed asynchronously.

Typical stages include:

1. Validation.
2. Preprocessing and silence filtering.
3. Proxy creation for playback.
4. Transcription.
5. Diarisation.
6. Merge, deterministic speaker resolution, and optional automatic AI enhancement.

### Processing ETA

When Nojoin has enough historical timing data from prior completed processing runs, the UI shows an estimated time remaining.

If not enough history exists yet, the interface says Nojoin is still learning instead of fabricating a number.

### Retry Processing

If a recording fails or you want to rebuild the generated meeting artefacts, use **Retry Processing**.

Retry Processing:

- Clears transcript-derived generated state.
- Rebuilds the meeting from the original audio.
- Preserves recording metadata, tags, uploaded documents, and user-authored notes.
- Records a fresh processing timing sample for future ETA calculations.

### Reprocess a Recording

From the recording detail page you can **Reprocess at higher quality**. This re-runs the full pipeline like Retry Processing after you change the transcription engine or model in Settings.

Reprocessing:

- Uses the transcription engine and model currently selected in Settings.
- Clears and rebuilds the transcript and generated artefacts, preserving metadata, tags, documents, and user-authored notes.
- Asks for confirmation before discarding the existing transcript.
- Use this when you want a different transcription pass from the one used during live capture.

## Transcript and Playback Workflow

Within a processed recording you can:

- Play the aligned web proxy.
- Follow synced transcript highlighting.
- Click transcript text to seek playback.
- Edit transcript text and speaker assignments.
- Export transcript-only, notes-only, or combined text output.

### Trimming a Recording

If you forgot to stop a recording and it carries trailing dead air (or a slow
start), you can trim it without altering the original audio.

- From the audio player on a processed recording, move the playhead and use
  **Set trim start** and **Set trim end** to mark the kept window.
- The player slider, the transcript view, and exports then reflect only the
  trimmed window.
- **Clear trim** restores the full recording everywhere.

Trimming is non-destructive: it stores two offsets and never re-encodes the
audio or changes the transcript. The downloaded audio file remains the full,
untrimmed recording.

## Speaker Management

Nojoin maintains a global speaker library across recordings.

Common workflows include:

- Linking an unknown in-recording speaker to an existing global speaker.
- Promoting a recording speaker into the People library.
- Creating or updating voiceprints.
- Recalibrating voiceprints from better samples.

Speakers with voiceprints display a fingerprint indicator in the UI.

## Meeting Intelligence

When an AI provider and model are configured, Nojoin can automatically generate and apply:

- Markdown meeting notes.
- A meeting title.
- Suggested renames for unresolved speaker labels.

This automatic enhancement happens once near the end of processing. If AI configuration is incomplete, the recording still finishes and remains fully reviewable, but the automatic AI stage is skipped.

Manual user notes are also used as supporting context for speaker suggestions and both automatic and manual note generation, and the final notes explicitly label user-authored items.

Meeting Edge is a separate live guidance flow. It does not wait for the end-of-processing meeting-intelligence pass, and it can use a cheaper provider-specific model than the main notes/title model. If the Meeting Edge model is unset, it falls back to the provider's main configured model.

### Linked Calendar Event

A recording can be linked to a calendar event. When processing finishes, Nojoin auto-links the recording to a calendar event from your selected calendars if there is a single, confident time overlap; it never links an ambiguous, all-day or zero-duration event and never overwrites a link you set yourself. On the recording page you can link, change, or unlink the calendar event manually. The linked event's title, description, and attendee list are added as context to generated meeting notes and to speaker naming, so attendee names become candidate speaker names. On the dashboard calendar, a linked recording no longer appears as a separate orange Nojoin meeting card; only the calendar event is shown.

Use **Generate Notes** or **Regenerate Notes** from the notes panel to rebuild only the meeting notes from the current saved transcript and speaker labels. This uses the currently saved AI provider, API key, model, and Ollama URL settings. If the provider configuration is incomplete or the provider rejects the request, the recording remains available and the notes panel reports the failure.

Use **Retry Speaker Inference** from the recording actions when you want to rerun only the speaker-naming step from the current saved transcript. This does not regenerate the title or meeting notes.

Use **Retry Processing** only when you want to rebuild the full meeting artefacts from the original audio, including transcription, diarisation, deterministic speaker resolution, and the automatic meeting-intelligence stage.

### Meeting Chat

The chat feature lets you ask questions about a single recording and its associated uploaded documents.

It can also be used to rewrite or refine generated meeting notes.

## Search and Organisation

Nojoin supports:

- Recording tags.
- Full-text search across titles, notes, and transcripts.
- Fuzzy matching for tolerant search.
- Speaker-based filtering and navigation.

### Browse Recordings by Calendar Date

The recordings filter panel includes a month calendar. Days that have
recordings are marked with a dot, and clicking a day filters the recordings
list to that day. Use the arrows to move between months. The raw date inputs
(Range, After, Before) remain available as an alternative.

Day boundaries follow your configured timezone (see **Settings → Timezone**),
so a recording captured just before local midnight is grouped on the correct
local day rather than on a UTC day.

## Settings Most Users Will Use

### Personal Settings

Normal users mainly interact with:

- Profile details.
- Password change.
- Calendar connections.

### AI

Depending on permissions, users may also see or adjust:

- LLM provider selection.
- Provider model selection.
- Provider API keys or Ollama URL.
- Transcription engine selection (Whisper, Parakeet or Canary).
- Whisper model settings.
- Parakeet is much faster than Whisper on supported NVIDIA systems, but it may be slightly less accurate and supports fewer languages. Use Whisper when language coverage or accuracy is more important than speed.
- Local Ollama configuration.

The Personal settings area no longer exposes separate automatic title, notes, or speaker-inference toggles. Automatic AI enhancement runs whenever provider configuration is complete, and `Prefer Short Titles` remains the main user-facing behavior control for that stage.

### Timezone

The Personal settings area lets each user choose the IANA timezone used for dashboard calendar rendering and task deadlines.

### Updates

The Updates page shows:

- The installed server version from the current API build.
- The latest stable published release.
- Release history and release notes.
- Companion installer links.

### Help

The Help area includes:

- Tour reset actions.
- Demo meeting recreation.
- Issue reporting links.

## Related Docs

- [COMPANION.md](COMPANION.md)
- [CALENDAR.md](CALENDAR.md)
- [ADMIN.md](ADMIN.md)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md)
