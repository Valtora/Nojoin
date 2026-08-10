# Nojoin User Guide

This guide covers day-to-day use after Nojoin has been deployed and your account has been created.

For deployment, administration, calendar provider setup, backup operations, and detailed capture troubleshooting, use the dedicated guides in the `docs` folder.

## First Run

1. Open Nojoin in Chrome on Windows, Linux, or macOS for shared-audio recording, another supported desktop Chromium browser, or Chrome on Android/iOS for microphone-only recording.
2. Sign in with your account.
3. Open **Settings > Recording** if you need to choose a microphone or adjust shared-audio and microphone gain.
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
- **Calendar** month grid, showing which days carry events or recorded meetings.
- **Agenda**, showing what is happening on the selected day or across the viewed month.
- **Task List** for quick personal follow-up capture.
- **Processing**, listing whatever the pipeline is still working on. It appears only while
  something is in flight.
- **Recent Meetings**, the latest ones with a click through to the detail view. It fills the height
  of its column and scrolls, and appears only once you have recordings.

The first four always render. The last two appear when they have something to report, so a new
account sees a dashboard rather than a page of empty boxes. The dashboard adds a second and then a
third column as space allows, measured against the workspace itself, so collapsing the navigation
rail can gain you a column at the same window size.

On desktop viewports around `1920x1080` and smaller, Nojoin automatically shifts into a denser desktop layout so more dashboard, recordings, transcript, notes, and settings content remains visible without affecting the roomier large-monitor layout.

### Calendar Surface

- The month grid and the agenda are separate modules and are both visible at once; there is no view toggle.
- The agenda opens on today. Selecting a day in the month grid scopes the agenda to that day, and **Whole month** in the agenda header returns it to the full month.
- Use **Today** to jump back to the current date context.
- A day agenda for today shows a live now marker against timed events.
- Unlinked Nojoin recordings appear on the dashboard calendar as orange meeting items, while Google or Microsoft calendar sources keep their own colours.
- Recorded meeting cards surface tags, speakers, and timestamps directly inside the selected-day agenda.
- The agenda view is month-scoped and includes both synced calendar events and unlinked Nojoin meeting history for the viewed month.
- In the current month, the agenda starts at the next upcoming item; use **Show past events** to expand earlier items. Past months always show their full history.
- Clicking a timed event bubble in the day timeline opens a details popover with the full title, time, calendar, location, join link, and linked recordings.
- Live events with a trusted meeting link show a **Join** button directly on the timeline bubble.
- Meeting links render as compact **Join meeting** labels with the provider host rather than raw URLs; the full URL appears on hover.
- Events continuing past midnight show dashed edges and chevrons on the day timeline.
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
- Organise people with reusable people tags.
- Open batch editing and cleanup flows for broader speaker-library maintenance.
- Delete a person to remove them from the library. Deletion demotes rather than erases: the shared record, its voiceprint, and its contact details are removed, while each of their meetings keeps the speaker under the same name as a recording-local speaker.

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

While recording, Nojoin shows recording state, duration, upload state, a live waveform, a live transcript panel, Meeting Edge guidance, your live notes panel, and collapsed processing visibility.

The recording workspace lays these out so that on a wide display everything is visible at once rather than down a long scroll. The capture controls sit in a toolbar across the top, carrying the meeting's working name, the transport, **Upload** for attaching a document, and the speaker limit. Below it are two columns: the live transcript with your notes under it on the left, and Meeting Edge on the right. It collapses to a single stack on smaller screens. When you press **Stop**, the transcript becomes pipeline progress and the rest of the layout stays where it is.

Documents you attach are listed under your notes. The panel appears only once something is attached, since uploading is done from the toolbar.

On a processed meeting, the tabs are Transcript, Notes, Documents, and Analytics on every screen size. On mobile the speaker panel is opened from the actions menu in the meeting header rather than from a tab of its own, because five tab labels do not fit legibly on a phone. On desktop it stays in the side column beside the transcript.

On mobile and narrow tablet layouts, Nojoin uses compact navigation with a menu button on the main dashboard surfaces. The active recording workspace and processed recording detail view both provide a native back control so you can return to the recordings list without relying on the browser's history buttons. Mobile Meeting Chat also includes its own back action to return to the meeting workspace.

You can switch to another browser tab, window, or application while recording. Nojoin only pauses automatically when the Nojoin tab is refreshed, closed, or navigated away from the active recording.

Keep the Nojoin tab open and the device awake for the length of the meeting. If the browser or the operating system suspends the tab, capture stops receiving audio for that period and it cannot be recovered afterwards, even though the recording is not paused. Nojoin warns you when the audio it has stored falls behind the elapsed recording time. See [Recorded audio is shorter than the meeting](CAPTURE.md#recorded-audio-is-shorter-than-the-meeting).

While a recording is active, a floating badge appears at the top-centre of the viewport on every page. The badge shows the recording status, elapsed time, and pause, resume, and stop controls. Clicking the badge navigates to the recording detail page. You can control the recording from any page without navigating back to the recording workspace first.

### Pause, Resume, Stop, And Discard

- **Pause** temporarily stops capture while preserving uploaded segments.
- **Resume** opens the browser share picker again and continues the same recording.
- **Stop** finalises the recording and starts processing. It names the stage it is working through, and it works on a paused recording as well as an active one.
- **Discard** permanently removes an in-progress recording in one step. It stops capture, cancels any processing, deletes the captured audio, and removes the meeting. Nojoin asks you to confirm first because this cannot be undone.

Discard is available from the live recording controls, the floating recording badge, the resume-or-discard modal, and the recordings menu, so you can abandon a recording from wherever you are.

If the browser is closed, refreshed, or loses the active recording page during capture, Nojoin pauses the recording to protect already uploaded data. When you return, Nojoin requires you to deal with that recording before starting anything else, offering three choices: resume it, **Stop and process** to keep the audio already captured and start processing, or discard it. Stopping this way does not reopen the browser share picker, so it works even though the original capture session is gone.

Paused recordings are retained indefinitely until you resume, stop, or discard them.

### Live Transcription

While a meeting is being recorded, the recording page shows a **Live Transcript** panel above Meeting Edge. Sentences appear as they finalize, a few seconds behind the conversation, because transcription works on windows of audio rather than decoding word by word. The panel follows the newest line automatically; scroll up to read back and it stops following until you scroll to the bottom again or use **Jump to latest**.

The panel is read-only. Live text is provisional and is corrected by the authoritative processing pass when the recording is finalized, so you cannot edit it during capture.

Live text is not attributed to speakers. Speaker identification runs when the recording is processed, so names, and any merging of speakers who were split during the meeting, appear only in the final transcript. Where Nojoin can tell which audio source a line came from it labels the line **Microphone** or **Shared audio**, and it labels nothing when people were talking over each other or the source was unclear. These labels describe where the audio came from, not who was speaking: if you are recording an in-person meeting without sharing tab audio, everyone in the room arrives on the microphone channel.

The live lane also feeds Meeting Edge and speeds up later processing. The full, corrected transcript appears once the recording has been finalized and the authoritative processing pass has produced review-ready output.

### Meeting Edge

Meeting Edge uses the recent live transcript window, an internally maintained rolling summary of the meeting so far (decisions, open threads, and action items), its own previous suggestions (so guidance stays fresh instead of repeating), your optional focus text, your manual notes, and linked calendar context when available.

It can surface live questions, missed points, and quick concept help during a meeting. In **Settings > Notes and live assistance**, everyone can tune the per-user **Technical context** slider to make concept explanations stricter or more detailed; administrators can additionally enable or disable Meeting Edge install-wide and choose a separate Meeting Edge model (if left empty, Nojoin reuses the main model).

## Importing Recordings

You can import existing audio files directly through the web client.

Supported formats include WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA, and OPUS.

The import flow validates the file, builds the canonical media artifacts, and queues background processing. Imports skip the live capture workflow but share the same final processing pipeline as live recordings.

### Discard Recording

Use **Discard Recording** from the recordings list or recording actions when a meeting is still recording, paused, queued, or processing and you no longer want it.

Discard Recording:

- Revokes any running processing task for that meeting.
- Closes any active live upload or finalisation session.
- Deletes the captured audio and derived files.
- Removes the recording entirely, so there is no leftover `Cancelled` entry to clean up afterwards.

Because it permanently deletes the meeting, Nojoin asks you to confirm before discarding. To remove a meeting that has already finished processing, use **Delete** instead.

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
- Export transcript-only, notes-only, or combined output as TXT, PDF, or DOCX.
- Export the recording's audio as MP3.

Exported transcripts carry a `[MM:SS]` timestamp and the resolved speaker name on every line, in all three document formats, so an export stays navigable against the audio without a separate subtitle file. Notes-only exports carry the meeting notes as written. Every export is headed with the meeting name, date, duration, and speaker list.

Historical recordings carried forward from before the unified pipeline cutover may open in a read-only compatibility state. Playback, transcript viewing, and export remain available, but transcript or speaker edits require explicit reprocess first.

### Who Edited A Line

An edited utterance carries a small label. `Edited` means the line was changed by hand in the web app. `AI corrected text` or `AI corrected speaker` means an assistant connected through the MCP connector made that change, so a correction an assistant made is never mistaken for one you made yourself. The labels survive reprocessing, and clearing an utterance's edit lock clears them along with it.

Edits made before this labelling existed show as `Edited`, because there is no record of which surface made them. Recordings in the read-only compatibility state above also show `Edited` only, since the connector cannot edit them at all.

Naming a speaker is disclosed differently, because it relabels every line that speaker holds. A speaker an assistant named carries a single `AI named` label in the speaker panel rather than a label on each utterance.

## Speaker Management

Nojoin maintains a global speaker library across recordings.

Common workflows include linking an unknown in-recording speaker to an existing global speaker, promoting a recording speaker into the People library, creating or updating voiceprints, recalibrating voiceprints from better samples, and merging duplicate speakers.

Inferred speaker names from final processing and manual retry flows are applied automatically to speakers that do not already have a trusted name. Manual speaker edits are authoritative: renaming, merging, promoting, or deleting a speaker always overrides inferred names, and speakers you have already named are never renamed by inference.

Renaming a speaker updates the transcript and the AI meeting notes together, so a meeting does not end up calling the same person two different things. The transcript resolves names at read time and so is always current; the notes are rewritten in place, matching the previous name only where it stands as a whole word. Your own notes are never rewritten. Because the notes are prose, a display name that is also an ordinary phrase will be replaced wherever it appears, so prefer regenerating the notes after renaming a speaker whose old name was not a personal name.

### Limiting The Number Of Speakers

Speaker detection is automatic, and that is the default. Occasionally a long meeting comes back split into more speakers than there were people in the room, usually when someone's audio changes partway through.

The optional **Maximum speakers** field sets an upper bound for a single recording. Leave it empty for auto-detect.

- **Live recording.** The field sits with the recording controls and stays editable for the whole meeting. Speaker detection runs when you stop, so the value present at that moment is the one applied. If someone joins unannounced, raise it before stopping.
- **Import.** Set it in the import dialog alongside the meeting name and date.
- **Reprocess.** Reprocessing accepts a new value, so an already-processed meeting can be corrected without re-recording it. Note that reprocess rebuilds the transcript too, and clears speaker names you set by hand on that recording.

It is an upper bound, not an exact count. Setting 4 for a meeting with 3 speakers still returns 3. This is deliberate: forcing an exact count would split one person into two whenever the number is too high, which is the problem the field exists to solve. Setting the value **lower** than the real number of participants will merge people together, so when in doubt, guess high or leave it empty.

### When Voiceprints Need Rebuilding

Voiceprints are only comparable with others made the same way. When an upgrade improves how they are extracted, previously saved ones stop contributing to automatic identification until they are rebuilt from the original audio.

**This happens on its own.** Nojoin checks periodically and rebuilds affected voiceprints from the original audio in the background, a few meetings at a time so the work does not compete with recording or processing. There is nothing to enable and no button to press. A large library repairs itself over several passes rather than all at once, so identification may stay degraded for a while after an upgrade before returning to normal.

Not every voiceprint can be rebuilt. Some belong to meetings whose audio has been removed, and others belong to a speaker who no longer has any speech in the transcript, which can happen after a meeting is reprocessed and that speaker's turns are attributed elsewhere. Neither can ever be re-extracted, so those are cleared rather than retried forever. A cleared voiceprint was already unusable for automatic identification, so nothing that was working is lost. The person and the meeting speaker are kept, so you can still link them by hand or save a new voiceprint from a later meeting.

## Notes, Chat, Documents, And Search

Processed recordings can include Markdown notes, AI-generated meeting notes, meeting chat, uploaded documents, and transcript/document search.

- **Analytics** reports the meeting's speaking dynamics, measured from the transcript.
- **Notes** are stored with the recording and can be edited after processing.
- **Generate Notes** runs a notes-only AI pass when AI is configured.
- **Meeting Chat** answers questions from the transcript, notes, and linked documents.
- **Documents** can be uploaded to support meeting context, meeting notes, and later search.
- **Search** spans recordings, transcript text, notes, tags, and document content where available.

On desktop, the Speakers and Chat panels sit in a resizable column to the right of the transcript
and notes. Each collapses independently from the control in its header: collapsing one gives the
other the full column height, and collapsing both reduces the column to a slim strip of icons that
restores them. The recordings list can likewise be collapsed from the control beside its search
box. All of these choices persist across visits, so a laptop can keep the full width for reading a
transcript without giving the panels up for good.

### Analytics

The **Analytics** tab sits beside Transcript, Notes, and Documents on a processed meeting. It answers how the conversation actually went: who held the floor, who cut across whom, and how the balance shifted through the meeting.

Everything on the tab is measured from the transcript's own timings or the audio. Nothing is generated by AI, so it works on an installation with no AI provider configured at all, and it is available on every meeting already in your library rather than only on meetings recorded from now on. It appears once a meeting has finished processing: speakers are separated when a recording is finalised, so a meeting still recording has no speaker attribution to measure.

What it shows:

- **Speaking time, speakers, silence, and how much of the meeting was people talking over each other.**
- **Who spoke**, as a share of everyone's speaking time.
- **Who spoke when**, showing the balance across the length of the meeting. A meeting where one person led the first half and another led the second reads very differently here from an evenly split one, though both show the same overall share.
- **How the conversation moved**: turns taken, median and longest turn, interruptions in both directions, and how long each person leaves before replying. Selecting a longest turn plays the meeting from that point, so any figure can be checked against the audio.

Two things are worth understanding about the numbers.

**Shares are of speaking time, not of the meeting.** When two people talk at once, that time counts for both of them, so the shares add up to 100% of the speech rather than 100% of the meeting's length. Silence is reported separately.

**An interruption means starting to speak while someone else still had more than a moment left to say.** It is counted in both directions, because interrupting and being interrupted are different things. Reply times ignore gaps too short to be a decision, so a speaker who only ever replied instantly shows no reply time rather than a misleading zero.

**Some meetings cannot show interruptions at all.** A recording transcribed as one continuous channel, which is usual for an imported file, produces a transcript where every line follows the last and two people talking at once cannot be represented. Nojoin shows a dash rather than a zero in that case and says why: no overlapping speech in the transcript is not the same as nobody having interrupted.

#### How People Spoke

Alongside the measured conversation figures, Nojoin can measure **delivery**: each person's speaking pace in words per minute, how high their voice sat, how much it moved (in semitones, so a low voice and a high voice are compared fairly), and how often they paused inside their own turns.

This one reads the meeting's audio, so it is done on request rather than for every meeting. Select **Measure delivery** on the Analytics tab. Meetings recorded after this feature was added have it measured automatically when processing finishes; older meetings, and any meeting whose transcript you have edited since, offer the button.

Each figure carries a short reading beside it so you do not have to know what 197 words a minute means. Pace is described against ordinary conversation, which runs at roughly 120 to 150 words a minute. Pitch movement and pausing have no such yardstick — the same expressive range measures wider on a high voice than on a low one — so those are compared only with the other people in the same meeting, and someone who barely spoke gets no comparison at all. Pauses are shown as a rate as well as a count, so a long contribution does not read as a hesitant one. Pitch height is deliberately left undescribed: that is a property of somebody's voice rather than of how they used it.

**Delivery describes how someone spoke, not how they felt.** There is no emotion detection in Nojoin, and these numbers are not sentiment, mood, or engagement scores. Someone speaking quickly and loudly may be enthusiastic, rushed, or annoyed, and nothing here can tell you which. They are useful for noticing that one person consistently speaks twice as fast as everyone else, or that a normally animated speaker was flat in a particular meeting, and for nothing stronger than that.

Two limits worth knowing:

- **Loudness is only compared when it can be.** On a call, someone in your room arrives through your microphone and everyone else arrives through the meeting's audio, having been through a codec and the far end's automatic volume control. Comparing those tells you about equipment, not people, so Nojoin does not compare them and says why.
- **Quiet participants may be missing.** A speaker needs a reasonable amount of uninterrupted speech before these figures mean anything, so someone who only said a few words is left out rather than described from three seconds of audio.

#### When The Figures Depend On Speaker Attribution

Every figure is attached to a speaker, so a figure is only as good as the speaker separation behind it. When one person is split into two, their talk share is split too, and nothing about the number itself looks wrong.

Nojoin therefore shows a warning on the tab when the meeting carries signs of that: several speakers holding a very small share each, a lot of overlapping speech, a meeting that reached its speaker limit, or speakers you have not named yet. The warning links to the speaker panel, where merging or naming speakers corrects the analytics immediately, with no reprocessing.

No warning does not mean the attribution is certainly right, only that nothing obvious suggests otherwise. If a figure looks wrong, check the speakers first.

### Attaching Documents

Upload PDF, PowerPoint, Word, Excel, CSV, text, Markdown, or image files, up to 250 MB each. Documents can be attached at any point: from the Documents tab of a processed meeting, or from the Documents panel that appears below the live transcript while a meeting is still recording or processing.

Timing matters. Meeting notes are generated once, at the end of processing, and they use every document that has finished parsing by then. A document attached during the meeting is normally ready in time to be included on the first pass. A document attached afterwards cannot be, so the Notes tab shows a banner offering to regenerate. Regenerating is never automatic: it uses your AI provider and overwrites any edits you have made to the notes.

#### Visual Analysis

Text extraction alone misses most of what a slide deck or a scanned report actually carries. **Analyse visually with AI** is therefore on by default: each page is sent to your configured AI model, which transcribes the text, reconstructs tables, and describes charts and diagrams including their values.

- Turn it off per upload for a document that is genuinely plain text, or to avoid using AI provider quota. The file is still parsed, just without visual analysis.
- It cannot be turned off for image uploads, which have no text to extract without it.
- Your model must accept images. Every current hosted model from Anthropic, OpenAI, and Google does. If you use **Ollama**, you must select a vision-capable model (such as a `llava` or `-vision` variant) in **Settings > AI**; a text-only model cannot read images and Nojoin will fall back to text extraction.
- If visual analysis is unavailable, Nojoin falls back to local OCR before giving up. OCR runs on your own server, costs nothing, and sends nothing anywhere, so a scanned page stays searchable even with no AI configured at all. What it cannot do is describe a chart or a diagram, only transcribe the words, and the document card says so when it was used.
- If neither is available the document still parses from its own text layer, and the card explains what was skipped. Use **Parse again** on the document card once a suitable model is configured.

PowerPoint, Word, and Excel files are read structurally as well as visually. This recovers speaker notes, table cells, and the exact underlying values of native charts, none of which a purely visual read would capture reliably. For a deck built mostly from SmartArt or hand-drawn diagrams, exporting it to PDF before uploading gives better results, because every page is then rendered in full.

There is no page limit. A large document simply takes longer and uses more provider quota, and files above 20 MB say so before you confirm. Parsing runs in the background on its own worker, so it never delays a live meeting, and progress is shown per page on the document card.

### Choosing How Notes Are Structured

The default notes cover a summary, key decisions, action items, per-topic detail, and a miscellaneous section. That fits a project or status meeting well and fits a user interview or an incident review badly, so the structure is editable in **Settings > Notes and live assistance > Notes structure**.

- A structure is a list of Markdown headings and a short description of what belongs under each. It controls what the notes contain, how they are ordered, what terminology is used, and how much detail is captured.
- Accuracy rules are not editable. Nojoin always instructs the model never to invent facts, decisions, or attributions, to attribute statements to the participant who made them, to format tables so the editor can render them, and to start at the first section without repeating the meeting title. A custom structure changes what the notes contain, never how faithful they are.
- **Generate** drafts a structure for you. Describe the meetings you run and what you need out of them, and the AI writes a structure, a name and a description into the editor for you to review and edit. Nothing is saved until you save it.
- **Preview** shows the exact prompt a structure produces, protected parts included, using a short sample transcript. It makes no AI request.
- The recording title, date, duration, and participants are always supplied to the model, so a structure can ask for them without any placeholder syntax.
- Administrators can additionally publish install structures, which everyone can see and use but only an administrator can change. Anyone can copy one into their own list to vary it. One install structure can be marked the install default, which applies to every user who has not chosen their own.
- Your chosen structure applies to notes generated automatically after processing. On the recording page, the arrow beside **Regenerate Notes** generates with a different structure for that meeting only, without changing your default.
- When a Nojoin update improves the built-in structure, any copy made from it is marked as out of date and offers a reset. Nothing is changed for you automatically.

### Glossary

**Settings > Transcription > Glossary** holds project names, acronyms, products, and corrections for words the AI commonly mishears, one per line as `Term: meaning`. Administrators maintain an install glossary for the whole installation, and each user can add their own; the two are merged rather than replaced, and a personal entry wins where both define the same term.

The glossary is used when writing notes and by Meeting Edge when it explains a term during a live meeting. It does not change the transcript, which is produced by the speech model before the glossary is ever read.

### Tables In Notes

Generated notes present key decisions and action items as tables, since both are naturally tabular. The notes editor renders these as real editable tables rather than raw Markdown.

- The toolbar's table button inserts a table at a chosen size, and once the cursor is inside one it also adds and removes rows and columns, toggles the header row, and deletes the table.
- Drag a column border to resize it. A table wider than the pane scrolls sideways on its own rather than stretching the page.
- Press Enter inside a cell for a line break. Cells hold a single line of content by design, and merged cells are deliberately not offered: Markdown cannot represent either, and notes are stored as Markdown.
- Copying notes into Confluence, Word, or another rich-text editor preserves the table structure.
- DOCX exports contain native Word tables with a repeating header row, and PDF exports render bordered tables. Plain-text exports keep the Markdown pipe syntax.

Notes remain Markdown throughout, so a table written by hand in Markdown, generated by the AI, or pasted in from elsewhere is treated identically.

## Calendar Features

Nojoin can connect to Google Calendar and Microsoft Calendar when an administrator has configured provider credentials.

Calendar events can provide meeting context, dashboard agenda views, linked recording history, and Meeting Edge context. Read [CALENDAR.md](CALENDAR.md) before changing calendar provider settings or troubleshooting OAuth.

## AI Assistant Connections (MCP)

Nojoin includes a built-in MCP connector so AI assistants such as Claude can work with your meeting library on your behalf. It has two access tiers: read tools cover the whole library including semantic search across every meeting and document, and write tools cover recoverable changes such as organising recordings, managing tasks, correcting transcripts, regenerating notes, and maintaining People records. Permanent deletion is not available through the connector at all. Add `https://your-nojoin-domain/mcp` as a custom connector in the assistant and approve access on Nojoin's authorisation page. Active connections are listed under **Settings → Integrations → Connected apps**, where each one can be revoked. See [MCP.md](MCP.md) for setup, supported clients, and troubleshooting.

## Settings

Settings are grouped by task.

- **Profile**: account details and password changes.
- **Capture**: microphone selection, shared-audio gain, microphone gain, browser audio-processing toggles, and a local mic input test for browser recording.
- **AI**: per-user AI routing (the server's configured provider, or your own Claude or ChatGPT subscription), the server provider and model, Meeting Edge, automatic meeting intelligence, language, and secondary-provider fallback. Install-wide controls (provider, models, the Ollama endpoint, fallback, and "Enable Meeting Edge") are shown only to administrators; a non-admin sees a read-only summary of the active provider instead.
- **Transcription**: transcription backend and model choices. Administrators picking a model the server does not have yet are asked whether to download it now, so it is ready before the next recording, or to leave it until first use.
- **Calendar**: user calendar connections and timezone behaviour.
- **Help**: tours and support surfaces.
- **Admin**: user, invitations, CLI usage, system, provider, release, and maintenance settings for administrators.

### Language Preferences

Use **Settings > Transcription > Language** to configure two independent choices:

- **Transcription language** controls ASR. The default is **Auto-detect**. Whisper supports auto-detection or a forced language, Canary supports the listed forced languages, and Parakeet continues to use multilingual auto-detection without forced-language support.
- **Notes generation language** controls AI-generated meeting titles, Markdown headings, summaries, detailed notes, and action items. The default is **English**. British English, American English, the transcription language, another listed language, or a custom language/style instruction can be selected.

The LLM prompt and machine-readable response contract remain stable even when generated content is localized: JSON keys, speaker labels, and application-owned metadata are not translated.

Changing the transcription language affects new transcription work. Use **Reprocess at higher quality** to rebuild an existing recording under a different transcription-language preference. Changing the notes language does not translate saved notes automatically; run **Generate Notes** or reprocess the meeting to generate new notes.

Language preferences are per-user. Per-meeting overrides, full interface translation, Meeting Edge/chat language controls, and speech translation are not part of this setting.

### Your own subscription (CLI OAuth)

Instead of using the server's configured provider, you can route your own AI through your own **Claude** (Pro/Max) or **ChatGPT** (Plus/Pro) subscription — usually faster, and you can pick a stronger model. In **Settings > Your AI**, connect a subscription in the "Your AI subscription" panel, then set routing to **My own AI subscription**:

- **Claude** — open the grant link and paste back the code Anthropic shows you.
- **ChatGPT** — open the sign-in page, enter the code shown, and approve access; the panel finishes automatically once you approve.

Pick a model for notes/chat and, optionally, a faster live model for Meeting Edge. If you connect both, choose which one is active. When your subscription is unavailable or usage-limited, Nojoin falls back to the server's default provider chain — its primary provider first, then its secondary if that also fails. Once you start using it, the panel shows your recent token usage.

Because this uses your subscription quota, a cheaper model conserves *quota*, not money. If you reach your limit, Nojoin shows a reset time and falls back to the server's default provider chain — its primary provider first, then its secondary if that also fails. Disconnecting removes your stored credential.

### CLI usage and quota (admin)

Administrators can review per-user Claude-subscription usage under **Settings > AI providers > Usage and quota**: a searchable table of each user's token usage over the last 7 days, last 30 days, and lifetime, alongside their rate-limit status. Tokens count what Nojoin sent through each user's own subscription. A subscription exposes no live "remaining quota" figure, so the status column reflects the rate-limit signal (OK, approaching a limit, or limited until a reset time) rather than a balance. Usage is shown in tokens, never money — a subscription is flat-rate, so a per-turn currency figure would be misleading.

### Secondary LLM Provider

Nojoin supports configuring a secondary LLM provider as a fallback. When the primary provider fails with any error, the system automatically retries the request with the secondary provider. This applies to all AI features: Meeting Edge, meeting intelligence, speaker inference, and meeting chat.

The secondary provider has its own independent configuration:

- Provider selection (Gemini, OpenAI, Anthropic, or Ollama).
- Model and live model choices.
- API key or Ollama URL.

Configure the secondary provider through environment variables prefixed with `SECONDARY_` (e.g., `SECONDARY_LLM_PROVIDER`, `SECONDARY_GEMINI_API_KEY`). Leave `SECONDARY_LLM_PROVIDER` empty to disable fallback. The secondary provider configuration is visible in **Settings > AI providers** for administrators.

## Troubleshooting

- If live capture is unavailable, switch to Chrome on desktop for shared-audio recording or Chrome on Android/iOS for microphone-only recording.
- If remote participants are missing, start again and enable shared audio in the browser picker.
- If the microphone is missing, grant microphone permission and check **Settings > Recording**.
- If Nojoin reports a paused recording, resume it, stop and process it, or discard it before starting another capture.
- If processing fails, use **Retry Processing** or check the administrator logs.
- If calendar sync fails, review provider setup in [CALENDAR.md](CALENDAR.md).

## Updates

The Updates area shows the installed server version, latest available release, and release notes. Release metadata comes from GitHub Releases when the deployment can reach GitHub.
