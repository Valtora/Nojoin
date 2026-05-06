# Nojoin User Guide

This guide covers normal day-to-day use after Nojoin has already been installed.

For deployment, administration, calendar provider setup, and backup operations, use the dedicated guides in the `docs` folder.
For detailed Companion install, pairing, repair, re-pair, tray usage, and Firefox setup, use [COMPANION.md](COMPANION.md).

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
- Use `Start Pairing` for the first pair.
- Use `Generate New Pairing Code` when the code expires or when you are replacing an existing backend pairing. The current backend stays active until the new pairing succeeds.
- `Temporarily disconnected` means the pairing is still valid and should recover automatically.
- `Browser repair required` and Firefox support both route back to the native Companion app rather than running in the browser.
- Chrome and Edge are the default path. Firefox requires `Enable Firefox Support`, Firefox enterprise roots, a browser restart, and a fresh code.

## Tours and Onboarding

Nojoin includes guided tours for first-time users.

- The dashboard tour introduces navigation, recording, importing, companion setup, and settings.
- The transcript tour introduces the recording detail view when a recording is opened for the first time.
- These tours can be restarted later from the Help settings area.

## Dashboard

The root route is the operational home surface for Nojoin.

It brings together:

- **Meet Now** for live capture.
- **Recent meetings** for quick re-entry into recent work.
- **Calendar context** through month and agenda views.
- **Task List** for personal follow-up work.

### Calendar Surface

- Switch between month and agenda views.
- Use `Today` to jump back to the current date context.
- In month view, selecting a day opens a day agenda, and selecting today shows a live now marker against timed events.
- When calendars are connected, per-calendar colours help distinguish event sources.
- Event times render in your configured Nojoin timezone.
- If no calendar is connected, the dashboard shows an empty state instead of fake data.

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
- A persistent notes panel for user-authored context.

If live audio stays quiet for a while, Nojoin may show a low-key inline reminder near the waveform instead of a persistent fault-style warning. `Dismiss` hides that reminder for the rest of the current meeting, `Don't show again` suppresses it until you re-enable it, and `Settings > Audio & Recording > Audio Warnings` lets you reset the warning state later.

Manual notes are captured with low-latency autosave behaviour so typing remains responsive while the meeting is live.

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
6. Merge, inference, and note generation.

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

## Transcript and Playback Workflow

Within a processed recording you can:

- Play the aligned web proxy.
- Follow synced transcript highlighting.
- Click transcript text to seek playback.
- Edit transcript text and speaker assignments.
- Export transcript-only, notes-only, or combined text output.

## Speaker Management

Nojoin maintains a global speaker library across recordings.

Common workflows include:

- Linking an unknown in-recording speaker to an existing global speaker.
- Promoting a recording speaker into the People library.
- Creating or updating voiceprints.
- Recalibrating voiceprints from better samples.

Speakers with voiceprints display a fingerprint indicator in the UI.

## Meeting Intelligence

Nojoin can generate:

- Meeting summaries.
- Action items.
- Rich structured notes.
- Suggested speaker naming context.

Manual user notes are also used as supporting context for inference and final note generation, and the final notes explicitly label user-authored items.

Use **Generate Notes** or **Regenerate Notes** from the notes panel to rebuild only the meeting notes from the current saved transcript and speaker labels. This uses the currently saved AI provider, API key, model, and Ollama URL settings. If the provider configuration is incomplete or the provider rejects the request, the recording remains available and the notes panel reports the failure.

Use **Retry Processing** only when you want to rebuild the full meeting artefacts from the original audio, including transcription, diarisation, speaker resolution, title inference, and notes.

### Meeting Chat

The chat feature lets you ask questions about a single recording and its associated uploaded documents.

It can also be used to rewrite or refine generated meeting notes.

## Search and Organisation

Nojoin supports:

- Recording tags.
- Full-text search across titles, notes, and transcripts.
- Fuzzy matching for tolerant search.
- Speaker-based filtering and navigation.

## Settings Most Users Will Use

### Account Settings

Normal users mainly interact with:

- Profile details.
- Password change.
- Calendar connections.

### AI Settings

Depending on permissions, users may also see or adjust:

- LLM provider selection.
- Whisper model settings.
- Local Ollama configuration.

### Timezone

The General settings page lets each user choose the IANA timezone used for dashboard calendar rendering and task deadlines.

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
