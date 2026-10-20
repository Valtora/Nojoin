# Nojoin User Guide

This guide covers day-to-day use after Nojoin has been deployed and your account has been created.

For deployment, administration, calendar provider setup, backup operations, and detailed capture troubleshooting, use the dedicated guides in the `docs` folder.

## First Run

For a first test recording, follow [Make Your First Recording](GETTING_STARTED.md#5-make-your-first-recording). [CAPTURE.md](CAPTURE.md) lists the supported browsers and covers choosing what to share.

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

The first four always render. The last two appear only when they have something to report.

**Settings > Appearance > Density** controls spacing. Automatic tightens the layout on desktop windows around `1920x1080` and smaller, Comfortable and Compact apply one spacing at every window size, and Dense is a further step for mouse-and-keyboard use. On a touch screen Dense tightens spacing only, so touch targets do not shrink.

### Calendar Surface

- The agenda opens on today. Selecting a day in the month grid scopes the agenda to that day, and **Whole month** returns it to the full month.
- The agenda shows synced calendar events and unlinked Nojoin recordings together. Nojoin recordings are orange; Google and Microsoft calendars keep their own colours.
- In the current month the agenda starts at the next upcoming item, and **Show past events** expands earlier ones. Past months show their full history.
- Clicking a timed event opens its details: time, calendar, location, join link, and linked recordings. A live event with a trusted meeting link shows **Join** directly.
- Event times use your configured Nojoin timezone.

Read [CALENDAR.md](CALENDAR.md) for connection and setup details.

### Task List

The Task List is a personal dashboard list for quick follow-up work.

You can create, rename, complete, reopen, archive, delete, and schedule tasks. Active deadlines show a live time-remaining badge. Archived tasks disappear from the dashboard immediately, including tasks that were already completed.

## Tasks Workspace

The **Tasks** page manages tasks more fully than the dashboard list:

- Select **Create Task** to open the task creation form when you need to add a richer task.
- **Open** shows active, non-archived tasks.
- **Completed** shows finished, non-archived tasks that can still be reopened.
- **Archived** shows hidden tasks and lets you restore them to the active task surfaces.
- Task cards support a title, body, deadline, permanent delete, direct recording links, and the same recording tag taxonomy used elsewhere in Nojoin.

Delete remains permanent. Use archive when you want to hide a task without losing it.

## People Workspace

The **People** page manages your shared speaker library:

- Review people records and stored voiceprints in one place.
- Search and filter the library when you need to find a known speaker quickly.
- Organise people with reusable people tags.
- Open batch editing and cleanup flows for broader speaker-library maintenance.
- Delete a person to remove them from the library. Deletion demotes rather than erases: the shared record, its voiceprint, and its contact details are removed, while each of their meetings keeps the speaker under the same name as a recording-local speaker.

## Live Recording

Live recording is browser-native: shared tab, window or screen audio plus your microphone on supported desktop Chromium browsers, and the phone microphone only on Chrome for Android and iOS. [CAPTURE.md](CAPTURE.md) covers starting a recording, choosing what to share, and keeping the tab recording.

While recording, the workspace shows the transport controls, a live waveform, the live transcript, your notes, and Meeting Edge. **Attach Docs** in the toolbar attaches documents while the meeting runs, and they are listed under your notes. When you press **Stop**, the transcript becomes pipeline progress.

A processed meeting has Transcript, Notes, Documents and Analytics tabs. The speaker panel sits beside them on desktop and opens from the meeting header's actions menu on a phone.

### Pause, Resume, Stop, And Discard

**Pause** keeps what has been uploaded, **Resume** reopens the share picker and continues the same recording, **Stop** finalises it and starts processing, and **Discard** deletes an unfinished recording, including one that is queued or processing, after asking you to confirm. To remove a meeting that has finished processing, use **Delete** instead.

If the tab is closed or refreshed during capture, the recording is paused, and Nojoin asks you to resume it, stop and process it, or discard it before you start anything else. [CAPTURE.md](CAPTURE.md#pause-resume-stop-and-discard) describes this in full.

### Live Transcription

While a meeting is being recorded, the recording page shows a **Live Transcript** panel. Sentences appear as they finalize, a few seconds behind the conversation, because transcription works on windows of audio rather than decoding word by word. The panel follows the newest line automatically; scroll up to read back and it stops following until you scroll to the bottom again or use **Jump to latest**.

The panel is read-only. Live text is provisional and is corrected by the authoritative processing pass when the recording is finalized, so you cannot edit it during capture.

Live text is not attributed to speakers. Speaker identification runs when the recording is processed, so names, and any merging of speakers who were split during the meeting, appear only in the final transcript. Where Nojoin can tell which audio source a line came from it labels the line **Microphone** or **Shared audio**, and it labels nothing when people were talking over each other or the source was unclear. These labels describe where the audio came from, not who was speaking: if you are recording an in-person meeting without sharing tab audio, everyone in the room arrives on the microphone channel.

The live lane also feeds Meeting Edge and speeds up later processing. The full, corrected transcript appears once the recording has been finalized and the authoritative processing pass has produced review-ready output.

### Meeting Edge

Meeting Edge uses the recent live transcript window, an internally maintained rolling summary of the meeting so far (decisions, open threads, and action items), its own previous suggestions (so guidance stays fresh instead of repeating), your optional focus text, your manual notes, and linked calendar context when available.

It can surface live questions, missed points, and quick concept help during a meeting. In **Settings > Notes and live assistance**, everyone can move their own **Technical context** slider towards fewer or more explained terms. Administrators turn Meeting Edge on or off for the whole installation, and can choose a separate Meeting Edge model, in **Settings > AI providers**; left empty, Nojoin uses the default model.

## Importing Recordings

You can import existing audio files with **Import Audio** at the top of the **Recordings** list. It is unavailable while a paused recording is waiting for you to resume, stop or discard it.

Supported formats include WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA, and OPUS, and the video and media containers MKV, MKA, MOV, AVI, M4V, TS, MTS, MPG, MPEG, and 3GP. Nojoin keeps only the audio of an imported file. A file with no audio track is refused: the import shows as failed and nothing of the file is kept.

An import goes through the same final processing as a live recording.

### Retry Processing

If a recording fails or you want to rebuild the generated meeting artifacts, use **Retry Processing**.

Retry Processing clears transcript-derived generated state, preserves recording metadata, tags, uploaded documents, and user-authored notes, then records a fresh processing timing sample for future ETA calculations.

If transcription itself fails, the recording is marked **Error** instead of finishing with an empty transcript, a notification says so, and the transcript tab explains what went wrong. Meeting notes cannot be generated for it until it is reprocessed. When the GPU ran out of memory the message says so; Nojoin has already retried once after freeing memory by then, so free GPU memory, or have an administrator choose a smaller transcription model for the installation under **Settings > Transcription**, before using Retry Processing. A meeting in which nobody spoke still finishes normally, and its transcript tab says that no speech was detected.

### Reprocess A Recording

From the recording detail page you can choose **Reprocess at higher quality**. This re-runs the full pipeline after an administrator changes the transcription engine or model, which apply to the whole installation, under **Settings > Transcription**.

Reprocessing clears and rebuilds transcript and generated artifacts while preserving metadata, tags, documents, and user-authored notes.

## Transcript And Playback

Within a processed recording you can:

- Play the aligned web proxy.
- Follow synced transcript highlighting.
- Click transcript text to seek playback.
- Edit transcript text and speaker assignments.
- Export any combination of the meeting notes, the analytics and the transcript as one PDF, Word or plain-text file. See [Exporting A Meeting](#exporting-a-meeting).
- Export the recording's audio as MP3. The option is unavailable when the recording has no audio, for example after a restore from a backup taken without it. While the playable copy is still being prepared, the export asks you to try again shortly.

### Exporting A Meeting

**Export** on a recording opens a dialog with three parts to include, in any combination: the meeting notes, the analytics and the transcript. The file always lists them in that order. Choose PDF, Word (DOCX) or plain text, or export the audio on its own as MP3.

- **PDF and Word files open on a Nojoin letterhead**: the logo and name, then the meeting title, its date and length in your own timezone, and who spoke. Later pages carry the meeting title in a running header, and every page has a footer with the page count. Word files embed the Geist typeface, so they look the same on a computer that does not have it installed.
- **Analytics are laid out as the Analytics tab shows them**, with the same figures, readings and charts. The explanations the tab keeps behind its **How this is measured** links are printed in small type under each section, because a reader of the file cannot click through to them. A part that has not been measured or analysed yet says so and names the button that runs it: exporting never starts a measurement or spends AI quota. Analytics can be exported once the meeting has finished processing.
- **Speakers keep their colours.** A person who is blue in the app is blue in the export's charts and transcript. A name whose colour is too light to read as text, such as yellow, is printed in a darker shade of the same colour.
- **Plain-text files keep the notes as the Markdown they are stored in**, with tables padded so their columns line up, and give the figures a chart plots instead of the chart.

Exported transcripts carry a `[MM:SS]` timestamp and the resolved speaker name on every line, in all three formats, so an export stays navigable against the audio without a separate subtitle file.

An administrator can turn the Nojoin branding off for the whole installation; see [Exported Files](ADMIN.md#exported-files).

Historical recordings carried forward from before the unified pipeline cutover may open in a read-only compatibility state. Playback, transcript viewing, and export remain available, but transcript or speaker edits require explicit reprocess first.

### Who Edited A Line

An edited utterance carries a small label. `Edited` means the line was changed by hand in the web app. `AI corrected text` or `AI corrected speaker` means an assistant connected through the MCP connector made that change, so a correction an assistant made is never mistaken for one you made yourself. The labels survive reprocessing, and clearing an utterance's edit lock clears them along with it.

Edits made before this labelling existed show as `Edited`, because there is no record of which surface made them. Recordings in the read-only compatibility state above also show `Edited` only, since the connector cannot edit them at all.

Naming a speaker is disclosed differently, because it relabels every line that speaker holds. A speaker an assistant named carries a single `AI named` label in the speaker panel rather than a label on each utterance.

## Speaker Management

Nojoin maintains a global speaker library across recordings.

Common workflows include linking an unknown in-recording speaker to an existing global speaker, promoting a recording speaker into the People library, creating or updating voiceprints, recalibrating voiceprints from better samples, and merging duplicate speakers.

Inferred speaker names from final processing and manual retry flows are applied automatically to speakers that do not already have a trusted name. Without an AI provider, a name is inferred only from a self-introduction whose name is capitalised, such as "I'm Priya", so a transcript in lowercase yields no inferred name. A line that opens with the name, such as "Priya here", counts only when the recording is linked to a calendar event that lists that person, because at the start of a line "Click here" looks the same. Manual speaker edits are authoritative: renaming, merging, promoting, or deleting a speaker always overrides inferred names, and speakers you have already named are never renamed by inference.

Renaming a speaker updates the transcript and the AI meeting notes together, so a meeting does not end up calling the same person two different things. The transcript resolves names at read time and so is always current; the notes are rewritten in place, matching the previous name only where it stands as a whole word. Your own notes are never rewritten. Because the notes are prose, a display name that is also an ordinary phrase will be replaced wherever it appears, so prefer regenerating the notes after renaming a speaker whose old name was not a personal name.

### Undoing An Automatic Merge

Processing sometimes joins two of the clusters speaker detection found into one speaker, because their voiceprints match. When that is wrong, the surviving speaker's card in the speaker panel lists each cluster that was merged in, with how many lines it holds, and an **Undo merge** button beside it. Undoing gives those lines back to a speaker of their own, named `Speaker N` until you rename it, and leaves every line you had already assigned by hand where you put it.

Only merges made by processing are listed. A merge you made yourself marks its lines as your edits, so undo one of those with **Split / Unmerge Speaker** or by reassigning lines. Recordings processed by an earlier version did not keep the clusters, so for those **Retry Processing** is the way back.

Processing never merges a cluster into a speaker it recognised from your People library unless the cluster also sounds like that person's saved voiceprint. A cluster that does not is left as its own `Speaker N`. If it was the same person after all, merge the two in the speaker panel.

### Limiting The Number Of Speakers

Speaker detection is automatic, and that is the default. Occasionally a long meeting comes back split into more speakers than there were people in the room, usually when someone's audio changes partway through.

The optional **Maximum speakers** field sets an upper bound for a single recording. Leave it empty for auto-detect.

- **Live recording.** The field sits with the recording controls and stays editable for the whole meeting. Speaker detection runs when you stop, so the value present at that moment is the one applied. If someone joins unannounced, raise it before stopping.
- **Import.** Set it in the import dialog alongside the meeting name and date.
- **Reprocess.** Reprocessing accepts a new value, so an already-processed meeting can be corrected without re-recording it. Note that reprocess rebuilds the transcript too, and clears speaker names you set by hand on that recording.

It is an upper bound, not an exact count. Setting 4 for a meeting with 3 speakers still returns 3. This is deliberate: forcing an exact count would split one person into two whenever the number is too high, which is the problem the field exists to solve. Setting the value **lower** than the real number of participants will merge people together, so when in doubt, guess high or leave it empty.

### Tuning Speech Detection And Speaker Separation

The defaults suit most meetings. Large meetings are harder: quiet or distant voices can be muted as noise before they are transcribed, and people who say one or two things can be folded into someone else. **Settings > Recording > Advanced > Speech and speaker tuning** lets you adjust the values behind both for your own recordings.

Every field starts empty, which means inherit: your installation's value if an administrator set one, otherwise the default shown in the field. A value saved earlier that is no longer accepted, for example after an upgrade narrowed its range, also shows as empty, but until the page next saves it uses the shipped default rather than your installation's value. **Reset to defaults** empties them all again. Changes apply to recordings processed after the change, and to any recording you reprocess; finished transcripts are not rewritten.

- **Speech detection threshold** (default 0.5, 0.15 to 0.90). How confident voice activity detection must be before audio counts as speech. Lower it if quiet speech goes missing from transcripts; raise it if background noise turns into words. The live transcript uses it too, even when voice activity detection is switched off for processing.
- **Word length cap** (Parakeet only, default 0.4 seconds, 0.2 to 0.45). The longest a word lasts. Parakeet reports when each word starts but not when it ends, so a word ends at the first of: the next word's start, the end of the stretch of audio it was transcribed in (at most 30 seconds), the first silence that voice activity detection left in the processed audio, and its start plus this value. The live transcript, and processing with voice activity detection switched off, usually have no such silences, so only the other three apply there. A longer word is more likely to overlap its speaker's turn, which helps a short reply land on the right person, but where there is no pause before the next speaker it can also reach into their turn. Canary reports no word timings and Whisper reports its own, so both ignore it.
- **Phantom speaker filter**. A detected speaker with no more than **Max speech** seconds (default 3) and no more than **Max segments** (default 3) is checked against the others. If it sounds like no one, below the **Non-speech floor** (default 0.35), it is treated as noise and its audio given to the nearest speaker. If it sounds like an existing speaker, at or above the **Merge similarity** (default 0.60), it is merged into them. In between, it is kept as a brief speaker. To keep more brief speakers, lower the two limits or raise the merge similarity. Setting either limit to 0 turns the filter off. The floor must stay below the merge similarity; for a field you leave empty, that means your installation's value, which the error names.
- **Single-word flip smoothing**. A word no longer than **Max word length** (default 0.45 seconds), within **Max gap** (default 0.25 seconds) of the words either side, is given back to the speaker on both sides of it. This tidies diarization jitter, but it can also erase a one-word interjection. Lower the values to keep more of them; a max word length of 0 turns smoothing off. No Parakeet word lasts longer than the word length cap, and the cap's maximum is the default max word length, so with that default only the gaps decide for Parakeet. A one-word Parakeet reply with no pause on either side can therefore be smoothed away.

### When Voiceprints Need Rebuilding

Voiceprints are only comparable with others made the same way. When an upgrade improves how they are extracted, previously saved ones stop contributing to automatic identification until they are rebuilt from the original audio.

**This happens on its own.** Nojoin checks periodically and rebuilds affected voiceprints from the original audio in the background, a few meetings at a time so the work does not compete with recording or processing. There is nothing to enable and no button to press. A large library repairs itself over several passes rather than all at once, so identification may stay degraded for a while after an upgrade before returning to normal.

Not every voiceprint can be rebuilt. Some belong to meetings whose audio has been removed, and others belong to a speaker who no longer has any speech in the transcript, which can happen after a meeting is reprocessed and that speaker's turns are attributed elsewhere. Neither can ever be re-extracted, so those are cleared rather than retried forever. A cleared voiceprint was already unusable for automatic identification, so nothing that was working is lost. The person and the meeting speaker are kept, so you can still link them by hand or save a new voiceprint from a later meeting.

## Notes, Chat, Documents, And Search

Processed recordings can include Markdown notes, AI-generated meeting notes, meeting chat, uploaded documents, and transcript/document search.

- **Analytics** reports the meeting's speaking dynamics, measured from the transcript, plus an optional AI reading of what was discussed and who owned each decision.
- **Notes** are stored with the recording and can be edited after processing.
- **Generate Notes** runs a notes-only AI pass when AI is configured.
- **Meeting Chat** answers questions from the transcript, notes, and linked documents. It reads the transcript with your edits and speaker names, without the unattributed lines (such as a removed speaker's) that the transcript view hides.
- **Documents** can be uploaded to support meeting context, meeting notes, and later search.
- **Search** spans recordings, transcript text, notes, tags, and document content where available.

On desktop, the Speakers and Chat panels share a resizable column beside the transcript. Each collapses from its header, the recordings list collapses too, and these choices persist across visits.

### Analytics

The **Analytics** tab sits beside Transcript, Notes, and Documents on a processed meeting. It answers how the conversation actually went: who held the floor, how quickly people took it from each other, how much they talked over each other, and how the balance shifted through the meeting. The evidence behind every figure and threshold on the tab is written up in [ANALYTICS_EVIDENCE.md](ANALYTICS_EVIDENCE.md).

Most of the tab is measured from the transcript's own timings or the audio. Nothing in those parts is generated by AI, so they work on an installation with no AI provider configured at all, and they are available on every meeting already in your library rather than only on meetings recorded from now on. The tab appears once a meeting has finished processing: speakers are separated when a recording is finalised, so a meeting still recording has no speaker attribution to measure.

One section at the bottom, [What Was Discussed](#what-was-discussed), is an AI reading rather than a measurement. It is optional, off until you ask for it, and sits on its own panel labelled **Read by AI, not measured** so it is never mistaken for the measured sections above it.

What it shows:

- **Speaking time, speakers, silence, and how much of the meeting was people talking over each other.**
- **Who spoke**, as a share of everyone's speaking time.
- **Who spoke when**, showing the balance across the length of the meeting. A meeting where one person led the first half and another led the second reads very differently here from an evenly split one, though both show the same overall share.
- **How the conversation moved**: turns taken, median and longest turn, how often each person took the floor the instant it opened, and how long they leave before replying when there is a gap to measure. Selecting a longest turn plays the meeting from that point, so any figure can be checked against the audio.
- **Talking over each other**, measured from the audio itself once the meeting has been measured: at least how much of the meeting was overlapping speech, and where it clustered. The figure is a floor. The detection model finds roughly 70 to 90% of overlapping speech, and every setting that finds more also counts overlap that was not there, so Nojoin reports the lower bound ([ANALYTICS_EVIDENCE.md](ANALYTICS_EVIDENCE.md)).

Everyone keeps the colour they have everywhere else in the meeting, so the person who is blue in the transcript is blue in every chart here; changing a speaker's colour in the speaker panel changes it here too.

Each figure carries a short line saying what it means, and the longer explanation of how it was arrived at sits under a **How this is measured** link beneath the section it belongs to. Nothing is hidden that changes what a number claims: the caveats that qualify a figure directly, such as overlapping speech being a floor rather than a total, stay in the sentence that reports it.

Three things are worth understanding about the numbers.

**Shares are of speaking time, not of the meeting.** When two people talk at once, that time counts for both of them, so the shares add up to 100% of the speech rather than 100% of the meeting's length. Silence is reported separately.

**Reply times are honest about what timestamps can measure.** An *instant handover* is taking the floor within a quarter of a second of the previous speaker stopping — quicker than transcript timing can resolve, and usually a reply prepared while the other person was still talking, so it is counted rather than timed. The reply-time median covers gaps long enough to measure; a turn taken after more than a few seconds of silence is a fresh start, not a reply, and is left out. Differences under a quarter of a second are noise.

**There are no interruption counts, on purpose.** The transcript writes speech down one line at a time, so it cannot represent two people talking at once; and working out from a single recording *who* cut across *whom* — and whether it was an interruption rather than agreement or encouragement — is not reliable enough to attach to a named colleague. What Nojoin measures instead is overlapping speech itself, from the audio: how much, as a floor (detection can miss overlap but never invents it), and where in the meeting. Nobody is named, and that is the point.

#### How People Spoke

Alongside the measured conversation figures, Nojoin can measure **delivery**: each person's speaking pace in words per minute, how high their voice sat, how much it moved (in semitones, so a low voice and a high voice are compared fairly), and how often they paused inside their own turns.

This one reads the meeting's audio, so it is done on request rather than for every meeting. Select **Measure delivery** on the Analytics tab; the same action also listens for overlapping speech. Meetings recorded after these features were added have both measured automatically when processing finishes; older meetings offer the button. A meeting whose transcript you have edited since it was measured is re-measured the next time you open its Analytics tab, so the figures match the text you corrected; turn **Re-measure delivery after edits** off under Settings > Recording to keep the old figures until you press the button yourself. When an upgrade improves how delivery is measured, Nojoin re-measures previously measured meetings by itself in the background, a few at a time — figures from different measurement methods are not comparable, so this is maintenance Nojoin owes its own data, like voiceprint rebuilding.

Each figure carries a short reading beside it so you do not have to know what 197 words a minute means. The pace words are anchored on measured recordings of English speech rather than the oft-quoted "conversation runs at 120 to 150 words a minute", which describes a different way of counting: by the measure Nojoin computes, recorded conversation sits around 160 to 200 words a minute, narration and presentations slower, and the band edges are pinned to published corpus figures ([the evidence note](ANALYTICS_EVIDENCE.md) lists them). Treat the words as a rough guide for meetings held in languages other than English — speaking rates in words are not comparable across languages. Pitch movement and pausing have no such yardstick — the same expressive range measures wider on a high voice than on a low one — so those are compared with the other people in the same meeting, and someone who barely spoke gets no comparison at all. Pauses are shown as a rate as well as a count, so a long contribution does not read as a hesitant one. Pitch height is deliberately left undescribed: that is a property of somebody's voice rather than of how they used it.

**Where Nojoin has measured enough of someone's meetings, it also compares them with themselves.** A speaker linked to a person in your People library, with at least three measured meetings behind them, gets a note when this meeting departs from their own usual figures — "faster than their usual across 5 meetings". The meeting count is always shown, because three meetings of history and thirty are different claims. This is the honest version of "a normally animated speaker was flat today": a comparison of measured delivery with measured delivery, not a guess about how anyone felt.

**Delivery describes how someone spoke, not how they felt.** There is no emotion detection in Nojoin, and these numbers are not sentiment, mood, or engagement scores. Someone speaking quickly and loudly may be enthusiastic, rushed, or annoyed, and nothing here can tell you which. They are useful for noticing that one person consistently speaks twice as fast as everyone else, or that a normally animated speaker was flat in a particular meeting, and for nothing stronger than that.

Two limits worth knowing:

- **Loudness is only compared when it can be.** On a call, someone in your room arrives through your microphone and everyone else arrives through the meeting's audio, having been through a codec and the far end's automatic volume control. Comparing those tells you about equipment, not people, so Nojoin does not compare them and says why.
- **Quiet participants may be missing.** A speaker needs a reasonable amount of uninterrupted speech before these figures mean anything, so someone who only said a few words is left out rather than described from three seconds of audio.

#### What Was Discussed

The rest of the tab is measured. This last section is different: it is an AI model reading the transcript, so it needs an AI provider and it uses your provider quota. Select **Analyse meeting** to run it. It is not run automatically unless you ask for that, because it answers questions most meetings are never asked and running it for every recording would spend your quota on meetings nobody opens. Your administrator may cap how many analyses you can start per day; the section says how many you have left and when the count resets (midnight UTC). Turn on **Analyse meetings automatically** under **Settings > Notes and live assistance** to have each new meeting analysed when it finishes processing, within that cap.

If your installation has no AI provider configured, this section says so and nothing else on the tab is affected.

It reports four things:

- **What the meeting covered**: the topics it moved through, in order, and who drove each. A topic that two people drove equally says so rather than picking one of them.
- **How people put things**: a reading of each person's *words* — what they backed, doubted, or objected to — with the quotes it is based on.
- **Questions asked**: who asked what, who answered, and which questions nobody answered. Unanswered questions are listed first.
- **Who owned each decision**: who proposed it, who agreed, who pushed back, and whether agreement was actually spoken.

Four things are worth understanding before you rely on any of it.

**Every claim about a person carries a quote, and you can play it back.** If the model could not point at something actually said in the transcript, the item was discarded rather than shown. This is deliberate and it is why the section is sometimes shorter than you might expect: a thin result means thin evidence, not a quiet meeting.

**"How people put things" is not how they sounded.** It reads language only. It is deliberately not combined with the delivery figures above, which measure the voice. Someone can make a positive point flatly, or a critical one warmly, and neither reading can tell you about the other.

**"Unchallenged" is not agreement.** A decision marked that way is one nobody objected to, which is a different thing from one people agreed with. Only people whose own words show it are listed as agreeing or pushing back; someone who said nothing about a decision appears in neither list.

**It complements the Notes tab rather than repeating it.** Notes remain the record of what was decided and what happens next. This section answers who: who drove the discussion, whose question went unanswered, who owned each decision. If the two ever seem to disagree about a decision, the quotes here are the thing to check.

If you edit the transcript afterwards, Nojoin shows a banner offering to analyse again rather than doing it for you, since that would spend your quota. A meeting too long to send in full is analysed as far as it fits, and the section says where it stopped.

#### When The Figures Depend On Speaker Attribution

Every figure is attached to a speaker, so a figure is only as good as the speaker separation behind it. When one person is split into two, their talk share is split too, and nothing about the number itself looks wrong.

Nojoin therefore shows a warning on the tab when the meeting carries signs of that: several speakers holding a very small share each, a lot of overlapping speech, a meeting that reached its speaker limit, or speakers you have not named yet. The warning links to the speaker panel, where merging or naming speakers corrects the analytics immediately, with no reprocessing.

No warning does not mean the attribution is certainly right, only that nothing obvious suggests otherwise. If a figure looks wrong, check the speakers first.

### Attaching Documents

Upload PDF, PowerPoint, Word, Excel, CSV, text, Markdown, or image files, up to 250 MB each. Documents can be attached at any point: from the Documents tab of a processed meeting, or with **Attach Docs** in the recording toolbar while a meeting is still recording or processing.

Several files can be attached at once. Browse to them or drag them onto the dialog, in one go or a few at a time, and each queued file is listed with its own **Analyse visually with AI** switch, so a deck can be read visually while the plain-text agenda beside it is not. Uploads run one after another rather than all at once, and the documents panel fills as they land. If one fails the rest still go through, and the dialog stays open with the failed files listed so they can be retried without re-sending what already uploaded.

Timing matters. Meeting notes are generated once, at the end of processing, and they use every document that has finished parsing by then. A document attached during the meeting is normally ready in time to be included on the first pass. A document attached afterwards cannot be, so the Notes tab shows a banner offering to regenerate. Regenerating is never automatic: it uses your AI provider and overwrites any edits you have made to the notes.

#### Visual Analysis

Text extraction alone misses most of what a slide deck or a scanned report actually carries. **Analyse visually with AI** is therefore on by default: each page is sent to your configured AI model, which transcribes the text, reconstructs tables, and describes charts and diagrams including their values.

- Turn it off per file for a document that is genuinely plain text, or to avoid using AI provider quota. The file is still parsed, just without visual analysis. When several files are queued, one control turns it off for all of them.
- It cannot be turned off for image uploads, which have no text to extract without it.
- Your model must accept images. Every current hosted model from Anthropic, OpenAI, and Google does. If you use **Ollama**, an administrator must select a vision-capable model (such as a `llava` or `-vision` variant) in **Settings > AI providers**; a text-only model cannot read images and Nojoin will fall back to text extraction.
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
- Administrators can additionally publish shared structures, which everyone can see and use but only an administrator can change. Anyone can copy one into their own list to vary it. One shared structure can be marked the shared default, which applies to every user who has not chosen their own.
- Your chosen structure applies to notes generated automatically after processing. On the recording page, the arrow beside **Regenerate Notes** generates with a different structure for that meeting only, without changing your default.
- When a Nojoin update improves the built-in structure, any copy made from it is marked as out of date and offers a reset. Nothing is changed for you automatically.

### Glossary

**Settings > Transcription > Glossary** holds project names, acronyms, products, and corrections for words the AI commonly mishears, one per line as `Term: meaning`. Administrators maintain a shared glossary for the whole installation, which everyone can read there, and each user can add their own; the two are merged rather than replaced, and a personal entry wins where both define the same term.

The glossary is used when writing notes and by Meeting Edge when it explains a term during a live meeting. It does not change the transcript, which is produced by the speech model before the glossary is ever read.

### Tables In Notes

Generated notes present key decisions and action items as tables, since both are naturally tabular. The notes editor renders these as real editable tables rather than raw Markdown.

- The toolbar's table button inserts a table at a chosen size, and once the cursor is inside one it also adds and removes rows and columns, toggles the header row, and deletes the table.
- Drag a column border to resize it. A table wider than the pane scrolls sideways on its own rather than stretching the page.
- Press Enter inside a cell for a line break. Cells hold a single line of content by design, and merged cells are deliberately not offered: Markdown cannot represent either, and notes are stored as Markdown.
- Copying notes into Confluence, Word, or another rich-text editor preserves the table structure.
- PDF and Word exports contain real tables, and a table's header row repeats at the top of each page the table runs onto. Plain-text exports keep the Markdown pipe syntax, padded so the columns line up.

Notes remain Markdown throughout, so a table written by hand in Markdown, generated by the AI, or pasted in from elsewhere is treated identically.

### Long Meetings And Ollama

A hosted provider (Anthropic, OpenAI or Gemini) reads a whole meeting in one call, however long it is. A local Ollama model reads only as much as its context window holds, and on a consumer GPU that is far less than a long meeting: the default window of 32,768 tokens covers about two hours.

**Recommendation:** do not rely on Ollama alone for long meetings. Use a hosted provider for them, or keep Ollama as the primary provider and configure a hosted [secondary provider](#secondary-llm-provider) with a larger context window. When a meeting does not fit Ollama's window, the secondary provider receives the whole meeting and returns notes in one call.

Without a secondary provider, or when it fails too, Ollama writes the notes in parts. Nojoin splits the transcript between utterances into parts that fit the window, writes notes for each part, then combines them into one set of notes with a title and speaker suggestions, as a normal run would. This takes several calls instead of one, so a long meeting takes several times as long to process, and the notes hold less detail than a single call's: the smaller the window, the more rounds of combining a meeting needs, and the less of the early detail reaches the final notes.

Meeting Chat on such a meeting answers from the notes and the passages of the transcript most relevant to your question, since the whole transcript does not fit. It is told to say so when those do not answer the question, so a question about a detail the passages missed can come back unanswered where a hosted provider would have found it.

## AI Assistant Connections (MCP)

Nojoin includes a built-in MCP connector, so AI assistants such as Claude can read your meeting library and make recoverable changes on your behalf. Add `https://your-nojoin-domain/mcp` as a custom connector in the assistant and approve access on Nojoin's authorisation page. [MCP.md](MCP.md) covers setup, the tools, and revoking access.

## Settings

Settings are grouped by subject, and each category is its own page. Every user has Profile and security, Appearance, Integrations (calendar connections and connected apps), Recording, Transcription, Notes and live assistance, Your AI, Updates, and Help; [ADMIN.md](ADMIN.md#settings-structure) lists the administrator categories as well. A search box finds an individual setting by name.

Most settings are your own and follow your account. A setting marked **This browser** is kept in the browser you set it in, such as the theme, the microphone and its levels, so each browser can differ. One marked **Everyone** applies to the whole installation, and only an administrator can change it.

### Language Preferences

Two independent choices, on two pages:

- **Transcription language**, under **Settings > Transcription**, controls ASR. The default is **Auto-detect**. Whisper supports auto-detection or a forced language, Canary supports the listed forced languages, and Parakeet continues to use multilingual auto-detection without forced-language support.
- **Notes language**, under **Settings > Notes and live assistance**, controls AI-generated meeting titles, Markdown headings, summaries, detailed notes, and action items. The default is **English**. British English, American English, the transcription language, another listed language, or a custom language/style instruction can be selected.

The LLM prompt and machine-readable response contract remain stable even when generated content is localized: JSON keys, speaker labels, and application-owned metadata are not translated.

Changing the transcription language affects new transcription work. Use **Reprocess at higher quality** to rebuild an existing recording under a different transcription-language preference. Changing the notes language does not translate saved notes automatically; run **Generate Notes** or reprocess the meeting to generate new notes.

Language preferences are per-user. Per-meeting overrides, full interface translation, Meeting Edge/chat language controls, and speech translation are not part of this setting.

### Your own subscription (CLI OAuth)

Instead of using the server's configured provider, you can route your own AI through your own **Claude** (Pro/Max) or **ChatGPT** (Plus/Pro) subscription. It is usually faster, and you can pick a stronger model. In **Settings > Your AI**, connect a subscription under **Your subscriptions**, then choose **My own subscription** under **How AI runs for you**:

- **Claude** — open the grant link and paste back the code Anthropic shows you.
- **ChatGPT** — open the sign-in page, enter the code shown, and approve access; the panel finishes automatically once you approve.

Pick a model for notes/chat and, optionally, a faster live model for Meeting Edge. If you connect both, choose which one is active. Once you start using it, the panel shows your recent token usage.

Because this uses your subscription quota, a cheaper model conserves *quota*, not money. If you reach your limit, Nojoin shows a reset time and falls back to the server's default provider chain — its primary provider first, then its secondary if that also fails. Disconnecting removes your stored credential.

### CLI usage and quota (admin)

Administrators can review per-user subscription usage under **Settings > AI providers**, in **Subscription usage** behind the **Advanced** block: a searchable table of each user's token usage over the last 7 days, last 30 days, and lifetime, alongside their rate-limit status. Tokens count what Nojoin sent through each user's own subscription. A subscription exposes no live "remaining quota" figure, so the status column reflects the rate-limit signal (OK, approaching a limit, or limited until a reset time) rather than a balance. Usage is shown in tokens, never money: a subscription is flat-rate, so a per-turn currency figure would be misleading.

### Secondary LLM Provider

Nojoin supports configuring a secondary LLM provider as a fallback. When the primary provider fails with any error, the system automatically retries the request with the secondary provider. This applies to all AI features: Meeting Edge, meeting intelligence, speaker inference, and meeting chat. A meeting too long for an Ollama primary's context window counts as such a failure, which makes a hosted secondary the recommended setup for long meetings with Ollama ([Long Meetings And Ollama](#long-meetings-and-ollama)).

The secondary provider has its own independent configuration:

- Provider selection (Gemini, OpenAI, Anthropic, or Ollama).
- Model and live model choices.
- API key or Ollama URL.

Configure the secondary provider through environment variables prefixed with `SECONDARY_` (e.g., `SECONDARY_LLM_PROVIDER`, `SECONDARY_GEMINI_API_KEY`). Leave `SECONDARY_LLM_PROVIDER` empty to disable fallback. The secondary provider configuration is visible in **Settings > AI providers** for administrators, where its model is chosen. The fallback stays off until it has a model. The card says so, and the collapsed **Advanced** block on that page reads **1 needs attention** until it is fixed.

## Troubleshooting

- If live capture is unavailable, switch to Chrome on desktop for shared-audio recording or Chrome on Android/iOS for microphone-only recording.
- If remote participants are missing, start again and enable shared audio in the browser picker.
- If the microphone is missing, grant microphone permission and check **Settings > Recording**.
- If Nojoin reports a paused recording, resume it, stop and process it, or discard it before starting another capture.
- If processing fails, use **Retry Processing** or check the administrator logs. A failed transcription shows its reason, such as the GPU running out of memory, on the recording's transcript tab.
- If calendar sync fails, review provider setup in [CALENDAR.md](CALENDAR.md).

## Notifications

Notifications appear in the bottom-right corner, in two kinds. Transient ones close after five seconds, or sooner from their close button. Persistent ones stay until you dismiss them: they report something that needs attention, such as a server that cannot be reached, and some carry buttons for the choices they ask you to make. **Notifications** in the navigation lists the last 100 shown in this browser.

## Updates

**Settings > Updates** shows the installed version, the latest release, and each release's notes. The installed and latest releases are open, and older ones expand when you choose them. Release information comes from GitHub Releases when the server can reach GitHub.

When a newer release is out, owners and administrators also get a persistent notification about it whenever they open Nojoin:

- **Go to Releases** opens that release on GitHub and leaves the notification up.
- **Skip Update** hides it for that release. The next release brings it back.
- **Remind Me**, or the close button, hides it for the rest of this sign-in. Reloading the page does not bring it back; signing in again does.

Skip Update is saved to your account, so a skipped release stays skipped on every device.
