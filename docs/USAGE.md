# Nojoin User Guide

This guide covers day-to-day use of Nojoin once it is deployed and you have an account. Deployment, administration, calendar setup, backups and capture troubleshooting have their own guides in this folder.

## First Run

For a first test recording, follow [Make Your First Recording](GETTING_STARTED.md#5-make-your-first-recording). [CAPTURE.md](CAPTURE.md) lists the supported browsers and covers choosing what to share.

Guided tours introduce the dashboard, the recordings list and the transcript view the first time you open each. **Settings > Help > Restart tours** shows them again, and **Create it again** brings back the Welcome to Nojoin demo meeting if you deleted it.

## Dashboard

The dashboard holds:

- **Meet Now**, where **Start Meeting** begins live capture.
- **Calendar**, a month grid marking the days that have events or recorded meetings.
- **Agenda**, for the selected day or the viewed month.
- **Task List**, for quick personal follow-ups.
- **Processing**, while anything is still being processed.
- **Recent Meetings**, once you have recordings.

**Settings > Appearance > Density** sets the spacing. **Automatic (by window size)** uses compact spacing on desktop windows up to 1920 by 1080 and comfortable spacing elsewhere, **Comfortable** and **Compact** use one spacing at every size, and **Dense** is tighter still for mouse and keyboard while keeping compact-sized controls on a touch screen.

### Calendar Surface

- The agenda opens on today. Select a day in the month grid to show only that day, and **Whole month** to return to the month.
- It lists synced calendar events with your unlinked Nojoin recordings, which are orange. Each calendar keeps its own colour.
- In the current month the agenda starts at the next item, and **Show _n_ past events** adds the earlier ones. Past months show everything.
- Click a timed event for its time, calendar, location, join link and linked recordings. A live event with a trusted meeting link shows **Join**.
- Times use the timezone set in **Settings > Appearance**.

[CALENDAR.md](CALENDAR.md) covers connecting a calendar.

### Task List

Create, rename, complete, reopen, archive and delete tasks, and give them deadlines. A task with a deadline shows a live time-remaining badge. Archiving a task removes it from the dashboard, whether or not it was completed.

## Tasks Workspace

The **Tasks** page has **Open**, **Completed** and **Archived** views and a **Create Task** button. A task has a title, a body, a deadline, linked recordings and tags, using the same tags as recordings. **Archive** hides a task and **Restore** brings it back. **Delete** is permanent.

## Recordings List

**Recordings** in the navigation lists your meetings, with **Archived** and **Deleted** views beside it.

- **Search...** above the list matches meeting titles, tags and speaker names. Its filter button adds a date filter (a range, after or before) and a speaker filter.
- **Tags** in the navigation hold your tags, which can nest. Select a tag to filter the list by it, drag a tag onto another to nest it, and right-click one to rename, add a sub-tag, promote or delete it. Tag a single meeting from its header. When a rename or move is refused, for example a name you already use or a tag dropped onto its own sub-tag, a notification gives the reason, and a refused rename keeps what you typed so you can correct it.
- Right-click a recording for **Rename**, **Retry Speaker Inference**, **Retry Processing**, **Show Recording Info**, **Archive** and **Delete**. An unfinished recording offers **Discard Recording** in place of **Retry Processing**.
- **Archive** moves a recording to **Archived**. **Delete** moves it to **Deleted**, the bin, where **Restore** brings it back and **Delete Permanently** removes it, its audio and its attached documents for good. Recordings stay in the bin until you delete them permanently.
- Ctrl-click (Cmd-click on a Mac) or Shift-click to select several recordings, then use the batch bar to **Add Tag**, **Archive**, **Delete**, **Restore** or **Permanently Delete** them.

## People Workspace

The **People** page is your speaker library: the people Nojoin can recognise across meetings, with their voiceprints and contact details. Each user has their own library, and a meeting's speakers are matched and linked only to people in its owner's library.

- Search by name, email or company, and filter by people tags, which can nest like recording tags. Each user has their own people tags and recording tags, and no two tags in either set share a name. A new or renamed people tag with a name already in use is refused with a notification, and the name you typed stays in its field.
- Select several people for **Batch Edit People** or to delete them together.
- A person's menu has **Edit Details**, **Recalibrate Voiceprint** to rebuild their voiceprint from better samples, **Split / Unmerge Speaker** and **Delete Person**.
- In a person's details, **Lock voiceprint** keeps their voiceprint as it is: renames, links, merges and automatic learning leave it alone, and only **Link to different speaker** in **Create Voiceprint**, recalibrating or splitting the person changes it. **Unlock voiceprint** lets them update it again. Neither changes the voiceprint itself, and a locked person is still recognised in later meetings.
- Deleting a person removes their record, voiceprint and contact details. Each of their meetings keeps the speaker under the same name, as a speaker of that meeting only.

## Live Recording

**Start Meeting** on the dashboard begins a recording. Desktop Chromium browsers record the shared tab, window or screen audio together with your microphone, and Chrome on Android and iOS records the phone microphone only. [CAPTURE.md](CAPTURE.md) covers supported browsers, choosing what to share, and the **Pause**, **Resume**, **Stop** and **Discard** controls.

While recording, the page shows the recording controls, a live waveform, the live transcript, your notes and Meeting Edge. **Attach Docs** in the toolbar attaches documents while the meeting runs or is processing. After **Stop**, the page shows processing progress.

Your notes save as you type and can be edited until notes generation starts. The AI reads them with the transcript, and the generated notes end with them under **User Notes**. A note holds up to 50,000 characters: a count appears near the limit, and longer text is not saved until you shorten it. Notes added on another device or by a connected assistant ([MCP.md](MCP.md)) are listed read-only under **Other notes**.

If the notes change on another device while you are editing them, nothing is saved over the other version until you choose **Keep mine** or **Use theirs** (**Discard mine** if they were deleted there). A change made offline is lost if you leave the recording before reconnecting.

**Discard** deletes an unfinished recording, including one that is queued or processing, after you confirm. To remove a finished meeting, use **Delete** in the recordings list. If the tab closes or reloads mid-recording, the recording pauses and Nojoin asks you to resume it, stop and process it, or discard it before you start anything else ([Paused Recording Lock](CAPTURE.md#paused-recording-lock)). If the pause could not reach Nojoin, because the tab was signed out or offline as it closed, the same dialog appears two minutes after the last audio arrived ([Closing Or Reloading The Tab](CAPTURE.md#closing-or-reloading-the-tab)).

A recording page that is still updating, while the meeting records or processes or its audio is being prepared, notices when the recording is discarded or deleted permanently elsewhere: it says so and returns you to the list. Moving the recording to the bin changes nothing on the page. If another tab has signed this browser in as someone else instead, the page stays and says once which account the browser is now signed in as; where it holds a recording or unsaved notes, it asks you to sign in again as their owner, within a minute for notes typed after that message. Reloading the recording on the page, for example by editing its title, does the same rather than leaving. Once the owner signs back in, the page carries on.

### Live Transcription

The **Live Transcript** panel shows each sentence a few seconds after it is spoken. It follows the newest line until you scroll up to read back; **Jump to latest** resumes following. Its lines are read-only, and the full transcript replaces them once processing finishes.

Live lines carry no speaker names, because speakers are identified when the recording is processed. If your administrator has turned on live speaker labels, lines show **Speaker 1**, **Speaker 2** and so on where the speaker changes. A label in italics can still change as Nojoin hears more of the meeting; once it is upright, it stays. A line with no clear speaker shows none. These numbers are only for following the conversation: the processed transcript identifies speakers afresh and may number them differently. A line is labelled **Microphone** or **Shared audio** when Nojoin can tell which source carried it. These labels name the audio source, not the person: in a room with no shared tab audio, everyone arrives on the microphone ([CAPTURE.md](CAPTURE.md#what-nojoin-captures)).

### Live Notes

The box under the live transcript adds a note at a moment in the meeting. Enter adds it and Shift+Enter starts a new line. The menu before the box, **Place note**, sets where it goes, and the line under the box says where the next note will land:

- **At typing start**, the default, places the note where the recording was when you typed its first character, however long you take to finish it. Clearing the box starts again.
- **At send** places it where the recording is when you press Enter.
- **Before the transcript** and **After the transcript** make a note on the whole meeting, kept with the Notes box. While the Notes box is empty, a note before the transcript becomes its text, which you can go on editing there. Otherwise it is listed under **Other notes**, like every note after the transcript.

A placed note shows on its own line, with its time, after the line being spoken when it was placed. A line still being transcribed can arrive after your note and appears above it. Time spent paused does not count, so a note written during a pause follows the last line before it. The processed transcript shows the note after the line spoken then, and the AI reads it there.

A note that could not be saved stays where you wrote it, marked with the reason, with **Retry** and **Discard**. Retrying never adds it twice, and never brings back a note deleted on another device. When the recording stops, here or on another device, text still in the box is kept, with **Add note** and **Discard**. Unsaved notes and text stay listed, with the processing progress and then above the processed transcript, until you save or discard them, and Nojoin asks before you close the page while any remain, where the browser supports that. Where the browser allows, they are also kept in it, so after a crash or a closed tab they come back when you open the recording again in this browser, and are sent once more without adding any twice. They are not sent until you do. This copy is only a safety net, and on browsers that don't ask (such as Safari on iPhone and iPad) the only cover for a closed tab: two tabs on one recording share it, the last to write wins, and a tab opened while another is live on the recording can bring back that tab's half-typed text. If your sign-in ends while a page holds unsaved notes, Nojoin asks for your password there instead of going to the sign-in page. The dialog also offers **Go to the sign-in page**, say after your username changed; your browser asks first, where it supports that. Signing out, or signing in as someone else, removes them from the browser. On a phone, the box stays above the on-screen keyboard.

The recordings list marks a recording **Unsent notes** while this browser holds notes, discards or box text for it that aren't saved yet. Open the recording to send them; a note that fails again is listed there with **Retry** and **Discard**. The list reads what the browser kept when the page loads and follows this tab's own notes after that, so a change made in another tab shows on the next page load. Notes kept for a recording since deleted for good get no mark, and stay in the browser until signing out or signing in as someone else removes them.

### Meeting Edge

Meeting Edge offers live guidance under **Questions to Ask**, **Points to Raise** and **Technical Context**. It works from the recent transcript, your notes, the linked calendar event and the [glossary](#glossary). Write a goal in **Guide Meeting Edge** to steer it. The **Technical Context** slider, on the recording page or in **Settings > Notes and live assistance**, sets whether it explains fewer or more terms.

An administrator can turn Meeting Edge off for the installation, or give it its own model, in **Settings > AI providers**.

## Importing Recordings

**Import Audio** at the top of the **Recordings** list imports an existing file, with an optional meeting name, date and **Max Speakers**. It is unavailable while a paused or interrupted recording is waiting for you to resume, stop or discard it.

Nojoin accepts WAV, MP3, M4A, AAC, WebM, OGG, FLAC, MP4, WMA and OPUS, and the media containers MKV, MKA, MOV, AVI, M4V, TS, MTS, MPG, MPEG and 3GP. It keeps only the audio. A file with no audio track is refused: the import shows as failed and nothing of the file is kept. An import is then processed like a live recording.

### Retry Processing

**Retry Processing**, in a recording's right-click menu in the recordings list, opens **Reprocess at higher quality**. Choose the transcription engine for this recording (Whisper, Parakeet or Canary), the Whisper model size if you chose Whisper, and optionally a new **Max Speakers**. Reprocessing replaces the transcript, the speaker labels and the AI meeting notes, so manual edits to those are lost. Your own notes, tags and documents are kept.

If transcription fails, the recording is marked **Error**, a notification says so, and the transcript tab shows the reason. When the GPU or the server ran out of memory, free some memory or retry with a smaller model. Notes cannot be generated until the recording is reprocessed. A meeting in which nobody spoke finishes normally, and its transcript tab says no speech was detected.

## Transcript And Playback

A processed meeting has **Transcript**, **Notes**, **Documents** and **Analytics** tabs. On desktop the **Speakers** and **Chat** panels share a resizable column beside them; on a phone, **Speakers** is in the header's actions menu. The header also holds the meeting's tags and its linked calendar event. The panels and the recordings list collapse from their headers and stay as you left them.

In the transcript:

- Playback highlights the current line, and the play button beside a line's timestamp plays from there.
- Click a line's text to edit it. Click a speaker name to reassign that line or the whole transcript, and double-click it to rename the speaker.
- The toolbar has **Undo**, **Redo**, **Search**, **Find & Replace** and export. In the find field, Enter and Shift+Enter, or the arrows beside it, move between matches. Escape closes the Search Options menu.
- **Low confidence** marks a line whose speaker Nojoin is unsure of and nobody has set by hand.
- Notes you placed during the meeting ([Live Notes](#live-notes)) show on their own line after the line they follow.

**Export** saves the notes, analytics and transcript as one PDF, Word or text file, or the audio as MP3 ([Exporting A Meeting](#exporting-a-meeting)).

A recording made by a much older version can refuse transcript and speaker edits until it is reprocessed. Playback, the transcript and export still work.

### Exporting A Meeting

**Export** opens a dialog. Tick any of **Meeting notes**, **Analytics** and **Transcript**, which the file always lists in that order, and choose **PDF (.pdf)**, **Word (.docx)** or **Text (.txt)**. **Export audio** saves the recording's audio as MP3 instead. It is unavailable when the recording has no audio, for example after a restore from a backup taken without it, and asks you to try again shortly while the playable copy is still being prepared.

- **PDF and Word files** open on a Nojoin letterhead with the meeting title, its date in your timezone, its length, and who spoke. Later pages carry the title in a running header, and every page has a footer with the page count. Word files embed their typeface, so they look the same on any computer.
- **Analytics** are laid out as on the Analytics tab, with each section's explanation printed beneath it. A part not yet measured or analysed says so and names the button that runs it: exporting never starts a measurement or uses AI quota. Analytics can be exported once processing has finished.
- **Speakers keep their colours** in charts and the transcript. A colour too light to read as text, such as yellow, is printed in a darker shade.
- **Text files** keep the notes as Markdown, with table columns padded to line up, and list a chart's figures in place of the chart.

Every exported transcript line carries a `[MM:SS]` timestamp and the speaker's name, in all three formats. An administrator can turn the Nojoin branding off ([Exported Files](ADMIN.md#exported-files)).

### Who Edited A Line

A line whose text was changed by hand in the web app is marked `Edited`. `AI corrected text` and `AI corrected speaker` mark changes an assistant made through the [MCP connector](MCP.md), and a speaker an assistant named shows `AI named` in the speaker panel. Edits made before Nojoin recorded where they came from show as `Edited`.

## Speaker Management

The speaker panel lists the meeting's speakers. Right-click one for **Rename / Assign**, **Merge into...**, **Split / Unmerge Speaker**, **Create Voiceprint**, **Add to People** and **Delete**.

- **Create Voiceprint** saves the speaker's voice and offers to link it to a person in your library, create a new person, or keep it to this meeting.
- **Add to People** promotes the speaker into your People library.
- **Delete** marks the speaker's lines as unknown. A person in your library stays there.

When processing finishes, Nojoin names speakers that have no name yet, and **Retry Speaker Inference** in the recordings list runs this again. With an AI provider it reads the meeting; without one, it uses only self-introductions with a capitalised name, such as "I'm Alice", so a lowercase transcript gets no names. Inference never renames a speaker you have named, and a rename, merge, promotion or deletion you make always wins.

Renaming a speaker updates the transcript and the AI meeting notes; your own notes are left alone. The notes change wherever the old name appears as a whole word, so if the old name was an ordinary phrase, regenerate the notes after renaming.

### Undoing An Automatic Merge

Processing sometimes merges two detected voices into one speaker because they sound alike. When that is wrong, the surviving speaker's card in the speaker panel lists each voice merged in, with its line count, beside an **Undo merge** button. Undoing gives those lines back to a speaker of their own, named `Speaker N` until you rename it, and leaves every line you assigned by hand where you put it.

Only merges made by processing are listed. To undo a merge you made yourself, use **Split / Unmerge Speaker** or reassign the lines. Recordings processed by versions that did not keep this record need **Retry Processing** instead.

### When A Voiceprint Is Not Saved

A person's voiceprint is saved only from a meeting where they have at least 20 seconds of clear speech, meaning their own talk without overlap. With less, renaming the speaker, **Add to People** and **Create Voiceprint** still link the person, and a notice says how much the meeting held, such as "Voiceprint not saved: 12 s of clear speech, 20 s needed." The next meeting where you link them to a speaker with enough speech saves it. Merging two speakers of one meeting adds their speech together.

Once a person has a voiceprint, a speaker you link later is blended in only if it sounds like them. If it doesn't, the notice says so; check the name. Nojoin then leaves that speaker out whenever it rebuilds the person's voiceprint, until you link it again and it is blended in, or recalibrate from its lines. The same check runs, with no notice, whenever a linked speaker's own voiceprint changes: **Create Voiceprint**, moving lines onto it, merging another speaker into it, or processing finishing for a speaker you named before it did. A result that doesn't sound like the person keeps that speaker out of their voiceprint, and one that does lets it back in. A locked voiceprint (**Recalibrate Voiceprint**, **Lock voiceprint** and a split lock it) stays as it is when you rename a speaker to that person, link one with **Add to People** or **Create Voiceprint**, or merge another person into them; a rename or link says in its notice that the voiceprint is locked. **Link to different speaker** in **Create Voiceprint** is the exception: it blends a speaker that sounds like them into the locked voiceprint and leaves it locked. Nojoin still checks whether that speaker sounds like them, as above, and a locked person who has no voiceprint still gets one. To let the speaker update it, choose **Unlock voiceprint** in the person's details and link the speaker again. Recalibrating or splitting a person uses only the meetings whose selected samples hold 20 seconds, so pick a good stretch from at least one meeting.

### Limiting The Number Of Speakers

Speaker detection is automatic. If a long meeting comes back with more speakers than there were people, usually because someone's audio changed partway through, set **Max Speakers** (1 to 50) for that recording. Leave it empty to auto-detect.

- **Live recording**: the field sits with the recording controls and stays editable throughout. The value when you press **Stop** is the one used, so raise it before stopping if someone joined unannounced.
- **Import**: set it in the import dialog.
- **Reprocess**: **Retry Processing** accepts a new value. It rebuilds the transcript too, which clears names you set by hand on that recording.

It is an upper bound: 4 for a three-person meeting still returns 3. A value below the real number of people merges people together, so when in doubt, guess high or leave it empty.

### Tuning Speech Detection And Speaker Separation

The defaults suit most meetings. In large ones, quiet or distant voices can be dropped as noise, and people who say only a word or two can be folded into someone else. **Settings > Recording > Advanced > Speech and speaker tuning** adjusts this for your own recordings.

Every field starts empty, meaning inherit: your installation's value if an administrator set one, otherwise the default shown. A saved value that is no longer accepted, for example after an upgrade narrowed its range, also shows as empty, but until the page next saves it uses the shipped default. **Reset to defaults** empties every field. Changes apply to recordings processed or reprocessed afterwards.

| Setting | Default | Range | Effect |
| --- | --- | --- | --- |
| **Speech detection threshold** | 0.5 | 0.15 to 0.90 | How confident voice activity detection must be before audio counts as speech. Lower it if quiet speech goes missing; raise it if background noise turns into words. The live transcript uses it too, even with voice activity detection off. |
| **Word length cap (Parakeet)** | 0.4 s | 0.2 to 0.45 | The longest a Parakeet word lasts; a word also ends where the next one starts. Raise it if short replies land on the wrong speaker; lower it if the last word of a sentence goes to whoever speaks next. Canary and Whisper ignore it. |
| **Phantom speaker filter**: **Max speech (seconds)**, **Max segments** | 3, 3 | 0 to 10, 0 to 20 | A speaker under both limits is checked against the others. 0 in either turns the filter off. |
| **Phantom speaker filter**: **Non-speech floor**, **Merge similarity** | 0.35, 0.60 | 0 to 0.95, 0.05 to 1 | A checked speaker below the floor is treated as noise and its audio given to the nearest speaker; at or above the merge similarity it joins the closest speaker; in between it is kept. The floor must stay below the merge similarity, and the error names your installation's value when it comes from a field you left empty. |
| **Single-word flip smoothing**: **Max word length (seconds)**, **Max gap (seconds)** | 0.45, 0.25 | 0 to 2, 0 to 1 | A word this short, this close to its neighbours, goes to the speaker on both sides of it. This tidies speaker jitter but can erase a one-word interjection. Lower the values to keep more; a max word length of 0 turns it off. |

To keep more brief speakers, lower the phantom filter's limits or raise its merge similarity. The same **Advanced** block has **Voice activity detection**, which filters silence and noise before transcription and can be turned off if quiet speech is cut off, and **Speaker separation**, which you can turn off for single-speaker recordings to speed up processing.

### When Voiceprints Need Rebuilding

When an upgrade changes how voiceprints are made, Nojoin rebuilds your saved voiceprints from the original audio in the background, a few meetings at a time. There is nothing to press. Until a voiceprint is rebuilt it is not used for automatic identification, so recognition can be weaker for a while after such an upgrade. A person whose meetings each hold less than 20 seconds of their clear speech keeps the outdated voiceprint, and is not recognised until you link them to a speaker in a meeting with enough.

A voiceprint that cannot be rebuilt, because its meeting's audio was removed or its speaker no longer has any speech in the transcript, is cleared. The person and the meeting's speaker remain, so you can link them by hand or save a new voiceprint from a later meeting.

## Notes, Chat, Documents, And Search

- **Notes** holds the AI meeting notes, which you can edit. **Generate Notes** (or **Regenerate Notes**) writes them again when AI is configured, overwriting your edits. Notes you type during the meeting are fed into the generated notes and kept when you reprocess.
- **Meeting Chat** answers questions from the transcript, with your edits and speaker names, the notes and the meeting's documents. **Context** in the chat header adds meetings with the tags you choose.
- **Documents** holds files attached to the meeting, which the notes and Meeting Chat draw on.
- **Analytics** reports how the conversation went, measured from the transcript and audio, with an optional AI reading of what was discussed.
- **Search** in the recordings list and **Search** and **Find & Replace** in the transcript are covered under [Recordings List](#recordings-list) and [Transcript And Playback](#transcript-and-playback).

### Analytics

The **Analytics** tab appears once a meeting has finished processing. Everything on it except [What Was Discussed](#what-was-discussed) is measured from the transcript's timings or the audio, with no AI, so it works on every meeting in your library and on an installation with no AI provider. [ANALYTICS_EVIDENCE.md](ANALYTICS_EVIDENCE.md) sets out the evidence behind each figure.

The tab shows:

- **Speaking time**, **Speakers**, **Silence** and **Talked over** totals.
- **Who spoke**: each person's share of the speaking time.
- **Who spoke when**: speaking time per speaker across the meeting.
- **How the conversation moved**: turns, median and longest turn, instant handovers and reply times. Select a longest turn to play the meeting from there.
- **Talking over each other**: how much of the meeting had overlapping speech, measured from the audio, and where it clustered.
- **How people spoke** and **What was discussed**, below.

Each figure has a one-line reading, and a collapsed note under each section explains how it was measured. Speakers keep the colours they have in the transcript and speaker panel.

Reading the figures:

- **Shares are of speaking time, not of the meeting.** Time when two people talk at once counts for both, and silence is reported separately.
- **An instant handover** is taking the floor within a quarter of a second of the previous speaker stopping. It is counted, not timed. Reply times cover the longer gaps; a turn taken after more than a few seconds of silence counts as a fresh start and is left out.
- **Overlapping speech is a floor.** The detector finds most of it but not all, so the figure reads "at least".
- **There are no interruption counts.** Who cut across whom cannot be told reliably from one recording, so Nojoin reports overlap without naming anyone.

#### How People Spoke

Delivery is each person's pace in words per minute, how high their voice sat, how much it moved in semitones, and how often they paused within their own turns. It reads the audio. New meetings are measured when processing finishes; for an older one, **Measure delivery** measures delivery and overlapping speech.

After you edit a transcript, its delivery figures are re-measured the next time you open the Analytics tab. Turn off **Re-measure delivery after edits** under **Settings > Recording** to keep the old figures until you choose **Measure again**. When an upgrade changes how delivery is measured, Nojoin re-measures older meetings in the background.

- Pace is read as deliberate, measured, conversational, brisk or fast. The bands come from English speech, so treat them as rough for other languages.
- Pitch movement and pausing are compared with the other people in the same meeting, and someone who barely spoke gets no comparison. Pitch height gets no reading.
- A speaker linked to a person in your People library, with at least three measured meetings, gets a note when this meeting departs from their usual figures, and the note gives the number of meetings behind it.
- Loudness is not shown, because it reflects each person's microphone and connection as much as their voice.
- Someone without enough uninterrupted speech is left out.

Delivery describes how someone spoke, not how they felt. Nojoin has no emotion detection, and these figures are not sentiment, mood or engagement scores.

#### What Was Discussed

This section is an AI model reading the transcript, labelled **Read by AI, not measured**. It needs an AI provider and uses your provider quota. Select **Analyse meeting** to run it, or turn on **Analyse meetings automatically** under **Settings > Notes and live assistance** to analyse each new meeting when it finishes processing. An administrator can cap analyses per person per day; the section then shows how many you have left and when the count resets (midnight UTC). Without an AI provider the section says so and the rest of the tab is unaffected.

It reports:

- **What the meeting covered**: the topics in order, and who led each, or that they were led jointly.
- **How people put things**: what each person backed, doubted or objected to, read from their words only.
- **Questions asked**: who asked what, who answered, and which went unanswered (listed first).
- **Who owned each decision**: who proposed it, agreed or pushed back, and whether agreement was spoken.

Every claim about a person carries a quote you can play back. An item the model could not support with a quote is dropped, so a short section means thin evidence. A decision marked **Unchallenged** is one nobody objected to, which is not the same as one people agreed with. After you edit the transcript, a banner offers **Analyse again**; nothing reruns on its own. A meeting too long to send whole is analysed as far as it fits, and the section says where it stopped. The Notes tab stays the record of what was decided; this section shows who drove it.

#### When The Figures Depend On Speaker Attribution

Every figure belongs to a speaker, so it is only as good as the speaker separation. When one person is split into two, their share is split too, and the number looks normal.

The tab warns when a meeting shows signs of this: several speakers with a very small share each, a meeting that reached its **Max Speakers** limit, or unnamed speakers. The warning links to the speaker panel, where merging or naming speakers corrects the analytics immediately, without reprocessing. No warning does not prove the attribution right, so if a figure looks wrong, check the speakers first.

### Attaching Documents

Attach PDF, PowerPoint, Word, Excel, CSV, text, Markdown, PNG or JPEG files, up to 250 MB each, from the **Documents** tab of a processed meeting or with **Attach Docs** while a meeting is recording or processing. Browse to several files or drag them onto the dialog. They upload one after another, and if one fails the rest still go through and the failed ones stay listed to retry.

Meeting notes are generated once, at the end of processing, from every document parsed by then. A document attached during the meeting is normally in time. One attached later is not, so the Notes tab shows a banner offering to regenerate. Regenerating uses your AI provider and overwrites your edits to the notes.

#### Visual Analysis

**Analyse visually with AI** is on by default. It sends each page to your AI model, which transcribes the text, rebuilds tables, and describes charts and diagrams with their values.

- Turn it off per file, or for every queued file at once, for a plain-text document or to save provider quota. The file is still parsed from its own text. It cannot be turned off for images.
- Files over 20 MB show a cost warning before upload. There is no page limit, and the document card shows progress per page.
- Your model must accept images. Current hosted models from Anthropic, OpenAI and Google do. With Ollama, an administrator must choose a vision-capable model (such as a `llava` or `-vision` variant) in **Settings > AI providers**.
- Without visual analysis, Nojoin uses OCR on your own server, which sends nothing anywhere and keeps scanned pages searchable but cannot describe a chart. Failing that, the document is parsed from its text layer. The document card says what was skipped; once a suitable model is configured, use **Parse again with visual analysis** on the card.
- PowerPoint, Word and Excel files are also read structurally, which recovers speaker notes, table cells and the values behind native charts. For a deck built mostly from SmartArt or drawn diagrams, export it to PDF first.

### Choosing How Notes Are Structured

**Settings > Notes and live assistance > Notes structure** sets the sections the AI writes notes into. The default has a summary, key decisions, action items, detailed notes and a miscellaneous section. A user interview or an incident review may want a structure of its own.

- A structure is a list of Markdown headings, each with a short description of what belongs under it. It controls what the notes contain, their order, terminology and level of detail.
- Accuracy rules are fixed: the model is always told never to invent facts, decisions or attributions, to attribute statements to the person who made them, to format tables so the editor can render them, and not to repeat the meeting title.
- **Generate structure** drafts one from a description of the meetings you run. Nothing is saved until you save it.
- **Preview** shows the full prompt a structure produces, using a sample transcript, without an AI request.
- The meeting's title, date, duration and participants are always supplied, so a structure can ask for them by name.
- Administrators publish shared structures that everyone can use; **Copy to my structures** makes your own copy to change. One shared structure can be the shared default for anyone who has not chosen their own.
- Your chosen structure applies to notes generated after processing. On the recording page, the arrow beside **Regenerate Notes** uses a different structure for that meeting only.
- When the Nojoin default improves, a copy made from it is marked with **Reset to the current default**. Nothing changes until you choose it.

### Glossary

**Settings > Transcription > Glossary** holds project names, acronyms, products and corrections for words the AI commonly mishears, one per line as `Term: meaning`. Administrators keep a **Shared glossary** that everyone can read; your own entries are added to it, and yours wins where both define the same term.

The glossary is used for notes and by Meeting Edge. It does not change the transcript.

### Tables In Notes

Generated notes present decisions and action items as tables, which the notes editor shows as editable tables.

- The toolbar's table button inserts a table at a chosen size. Inside a table it also adds and deletes rows and columns, toggles the header row and deletes the table.
- Drag a column border to resize it. A table wider than the pane scrolls sideways.
- Enter inside a cell adds a line break within the cell. Cells cannot be merged, because notes are stored as Markdown.
- Copying notes into Confluence, Word or another rich-text editor keeps the tables.
- PDF and Word exports contain real tables whose header row repeats on each page. Text exports keep the Markdown, padded so the columns line up.

### Long Meetings And Ollama

A hosted provider (Anthropic, OpenAI or Gemini) reads a whole meeting in one call. A local Ollama model reads only what fits its context window, and the default window of 32,768 tokens covers about two hours.

For long meetings, use a hosted provider, or keep Ollama as the primary and have an administrator add a hosted [secondary provider](#secondary-llm-provider), which receives any meeting too long for Ollama whole and returns notes in one call.

Without one, Ollama writes notes for parts of the meeting and combines them. That takes several times as long and keeps less detail, the more so the smaller the window. Meeting Chat on such a meeting answers from the notes and the passages of the transcript most relevant to your question, so it can miss a detail a hosted provider would find, and it says so when the passages do not answer.

## AI Assistant Connections (MCP)

Nojoin has a built-in MCP connector, so AI assistants such as Claude can read your meeting library and make recoverable changes for you. Add `https://your-nojoin-domain/mcp` as a custom connector in the assistant and approve access on Nojoin's authorisation page. [MCP.md](MCP.md) covers setup, the tools and revoking access.

## Settings

Each settings category is its own page. Every user has **Profile and security**, **Appearance**, **Integrations** (calendars and connected apps), **Recording**, **Transcription**, **Notes and live assistance**, **Your AI**, **Updates** and **Help**; [ADMIN.md](ADMIN.md#settings-structure) lists the administrator categories. **Search settings...** finds a setting by name, including one inside a collapsed **Advanced** block.

Most settings belong to your account. One marked **This browser** is kept in the browser you set it in, such as the theme and the microphone and its levels. One marked **Everyone** applies to the whole installation, and only an administrator can change it.

### Language Preferences

Two per-user settings:

- **Transcription language**, under **Settings > Transcription**, is the language spoken in your recordings. The default is **Auto-detect**. Whisper can detect a language or be forced to one, Canary takes the listed forced languages, and Parakeet always detects.
- **Notes language**, under **Settings > Notes and live assistance**, is the language of generated titles, headings, summaries, notes and action items, and of the AI meeting analysis. The default is English. Choose British or American English, the transcription language, another listed language, or **Custom** with your own instruction for language, conventions, tone and heading style.

Changes apply to new work. Reprocess a recording to transcribe it in a new language, and generate its notes again to change their language; saved notes are not translated. Neither setting translates the interface, Meeting Edge or chat.

### Your own subscription (CLI OAuth)

You can run your AI on your own **Claude** (Pro or Max) or **ChatGPT** (Plus or Pro) subscription instead of the server's provider, and choose the model yourself. In **Settings > Your AI**, connect one under **Your subscriptions**, then choose **My own subscription** under **How AI runs for you**.

- **Connect Claude**: open the link and paste back the code Anthropic shows you.
- **Connect ChatGPT**: open the sign-in page, enter the code shown and approve access; the panel finishes by itself.

Only one subscription can be connected at a time, so disconnect one before connecting the other. Choose a **Model** for notes and chat and, optionally, a separate **Meeting Edge model**. The panel shows your recent token usage.

Usage counts against your subscription's limits, so a cheaper model saves quota, not money. When your subscription is unavailable or rate-limited, Nojoin shows when it resets and the server's default provider answers, followed by its fallback if one is set. Disconnecting deletes your stored credential.

### Secondary LLM Provider

An administrator can set a fallback provider. When the primary provider fails with any error, including a meeting too long for an Ollama window, Nojoin retries the request on the fallback, for every AI feature. It is configured with the `SECONDARY_` environment variables ([DEPLOYMENT.md](DEPLOYMENT.md#configure-env)) and stays off until a model is chosen for it under **Settings > AI providers** ([ADMIN.md](ADMIN.md#ai-providers)).

## Notifications

Notifications appear at the bottom right. Most close after five seconds, or sooner from their close button. Persistent ones report something that needs attention, such as a server that cannot be reached, and stay until you dismiss them; some carry buttons for the choices they ask you to make. **Notifications** in the navigation lists the last 100 shown in this browser.

## Updates

**Settings > Updates** shows the installed version, the latest release and each release's notes, from GitHub Releases when the server can reach GitHub. The installed and latest releases are open, and older ones expand when you choose them.

When a newer release is out, owners and administrators get a persistent notification about it each time they open Nojoin:

- **Go to Releases** opens the release on GitHub and leaves the notification up.
- **Skip Update** hides it until the next release, on every device.
- **Remind Me**, or the close button, hides it until you next sign in. Reloading the page does not bring it back.

## Troubleshooting

- For capture problems, such as missing remote participants, a missing microphone or an unsupported browser, see [CAPTURE.md](CAPTURE.md#troubleshooting).
- A paused or interrupted recording blocks new capture until you resume it, stop and process it, or discard it.
- A recording that failed shows its reason on its transcript tab. Fix the cause and use **Retry Processing**; an administrator can check the logs in **Settings > System and logs**.
- For calendar sync problems, see [CALENDAR.md](CALENDAR.md#troubleshooting).
