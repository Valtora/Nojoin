# Browser Capture Guide

Nojoin records live meetings in the browser. This guide covers which browsers can record, what to share, and what to do when audio goes missing.

## Supported Environments

| Browser | Live capture |
| --- | --- |
| Chrome on Windows, Linux or macOS | Microphone only, or shared audio and microphone |
| Edge, Brave, Arc or another Chromium-family browser on Windows or Linux | Microphone only, or shared audio and microphone |
| Other Chromium-family browsers on macOS | Microphone only, or shared audio and microphone, best-effort |
| Firefox on Windows, Linux or macOS, or Safari on macOS | Microphone only |
| Chrome, Firefox, Safari or another browser on Android or iOS | Microphone only |

Shared-audio capture records the tab, window or screen you share plus your microphone. On Linux it needs PipeWire screen capture. Firefox, Safari and mobile browsers cannot share another tab's or app's audio, so they record only what the microphone hears and never open the share picker. Recording needs microphone access and AudioWorklet support, which current versions of these browsers all have.

In desktop Chromium, **Microphone only** records what your selected microphone hears without opening the share picker. Use it for an in-room meeting, a call on speaker or a voice memo. **Microphone and a tab or screen** keeps shared-audio capture. The source selector beside **Start Meeting** remembers the last choice in this browser, alongside the microphone settings. Until you choose, desktop Chromium uses shared audio and microphone. Firefox, Safari and phones always use the microphone alone and show no source selector.

On macOS, tab audio is the most reliable choice. Window and entire-screen audio work on current Chrome and current macOS when the share picker offers an audio toggle; older versions may share video with no audio. Other Chromium-family browsers on macOS are best-effort because their picker can lag Chrome's.

Browsers that cannot record can still play recordings, edit transcripts, manage speakers, search and administer Nojoin.

## What Nojoin Captures

Nojoin records two channels: the shared tab, window or screen audio, and your microphone. It uploads them as you record, in two-second lossless segments, using about 0.15 Mbit/s while one source carries sound and 0.3 Mbit/s while both do. The finished recording is a FLAC file exactly as long as the audio captured.

With **Microphone only**, the shared channel is silent and everyone is recorded through the microphone. Remote participants are recorded only if the microphone can hear them, on desktop as on a phone. Sharing a picture without its audio also leaves the shared audio channel silent.

While you record, the recording view shows a live waveform in the header and the **Transcript** tab a few seconds behind the conversation ([USAGE.md](USAGE.md#live-transcription)). The same view continues through pause, upload, queueing and processing. **Analytics** shows provisional talk time, speaker share, longest turns and transcript-derived pace estimates; silence, overlapping speech, audio delivery, pitch and AI analysis wait until after processing. **Chat** answers from the transcript so far with current speaker names and your timeline notes, warning that wording and speakers may still change. When the transcript is too long for the model, Chat uses its most recent part and says that it was trimmed. Previous answers stay as written.

Nojoin keeps none of the shared video. While you record, it reads one still from it only when you ask for a [capture](USAGE.md#captures), with the camera button beside the note box or Alt+Shift+C (Option+Shift+C on a Mac), and saves it on a note.

Microphone-only recording has no shared picture: the camera button is hidden and the shortcut does nothing. In desktop Chromium, a hint explains that screen captures need a shared tab, window or screen. You can still attach files to notes.

A segment the server cannot decode is set aside and leaves a two-second gap instead of stopping the meeting. The API logs it as a `segment_corrupt_skipped` pipeline metric.

## Starting A Recording

1. In desktop Chromium, open **Recording source** below **Start Meeting** on the dashboard's **Meet Now** card, beside it in the recordings list, or below the collapsed rail's microphone button. Choose **Microphone only** or **Microphone and a tab or screen**, then select **Start Meeting**.
2. For **Microphone and a tab or screen**, choose the meeting tab, window or entire screen in the browser's share picker, turn on its audio option if it offers one, and select **Share**. **Microphone only** skips this picker.
3. Allow microphone access if asked.
4. Keep the Nojoin tab open and the device awake until you stop.

The browser may switch to the tab or window you shared; Nojoin keeps recording in its own tab. Selecting **Cancel** in the picker returns you to where you were with no recording started. Before relying on Nojoin for an important meeting, record a short test and check that the live waveform moves.

In Firefox, Safari or on a phone, select **Start Meeting**; no share picker appears. Allow microphone access and keep the device near the meeting audio. On a phone, keep Nojoin in the foreground and stop the phone locking.

## Choosing Tab, Window, Or Entire Screen

- **Browser tab** suits Google Meet, Microsoft Teams, Zoom, Webex and other web meeting apps. Chrome and Edge offer **Share tab audio**. Some enterprise sign-in or protected meeting pages need you to share a different tab once sign-in completes.
- **Window** can work, but some browsers and operating systems give no audio for a window. If Nojoin then warns that no system or tab audio was detected, share the meeting tab instead.
- **Entire screen** suits a native meeting app or audio from several windows. On Windows, turn on **Also share system audio**. On Linux, screen audio depends on the browser, the desktop environment and PipeWire. On macOS, see [Supported Environments](#supported-environments).

Brave may need its shields or site settings relaxed before it shows capture prompts on a hardened profile. If **Share** is disabled, select a source first and check that any required audio toggle is on.

## Pause, Resume, Stop, And Discard

- **Pause** stops capturing and keeps everything recorded so far. The controls read **Paused** as soon as capture stops. By default, the microphone stays open. Turn on **Release the microphone while paused** in **Settings > Recording**, beside **Microphone**, to free it for another application while the recording is paused. Shared tab or screen audio stays open, although none is recorded while paused. If Nojoin cannot reach the server, it says so, keeps the recording paused in this tab, and tells the server once the connection returns. If the server refuses the pause, this tab stays paused and shows the server's reason.
- **Resume** continues the same recording. After a pause in the live controls it keeps any shared tab. If the microphone was released, Nojoin opens the microphone chosen in Settings again, and the browser may ask for permission ([Capture Settings](#capture-settings) covers a chosen microphone that has gone). A microphone that is busy, unavailable or refused permission leaves the recording paused with an explanation: reconnect it, close the application using it or allow microphone access, then choose **Resume** again. The same happens if the browser has not restarted audio for the tab within a couple of seconds, which a phone can hold back during a call. Recorded and queued audio stays safe. After the Nojoin tab was closed or reloaded, it uses the source chosen beside **Start Meeting** in this browser: with **Microphone and a tab or screen**, the browser asks you to share again.
- **Stop** releases the microphone and shared tab, uploads any audio still queued, and queues the recording for processing. While queued audio uploads, the controls read **Uploading** and show how much is left, and the meeting button names the current stage. Stop also works on a paused recording, including one whose tab was closed.
- **Discard** permanently deletes an unfinished recording, whether it is recording, paused, queued or processing: its audio, any attached documents, any processing under way and the meeting itself. Nojoin asks you to confirm first. Discard is in the live controls, the floating recording badge, the resume-or-discard dialog and the recordings list's context menu (**Discard Recording**).

Selecting the browser's own **Stop sharing** button, or closing its sharing indicator, stops and saves the recording with everything captured so far, and Nojoin tells you it did.

The recording keeps the same header and tabs through every stage. Its header shows the waveform and transport only when this tab owns that recording's capture. Opening another recording cannot control the capture from that recording's header; the global recording badge remains available. A second device sees server status, the transcript, notes and documents, with recovery actions in the global dialog.

After **Stop**, the header's player area shows the upload stage and backlog, then queue or pipeline progress, estimated time remaining and discard. The transcript continues updating below it through uploading, queued and processing, with its note composer underneath. **Speakers**, **My notes**, **Captures** and **Meeting Edge** stay in the side column on desktop and the main tab row on a phone; attached documents stay in **Documents**. A failed final read keeps the feed visible while Nojoin retries; an empty successful read replaces it too. Playback and export remain unavailable while the recording is in flight.

Switching tabs or windows, using another application or moving between Nojoin pages does not pause recording. On every page except the recording itself, a floating badge at the top of the screen shows the status and elapsed time, with pause, resume, stop and discard controls, and opens the recording when clicked.

The release setting has been tested in desktop browsers and in Chrome for Android, which records the microphone only. It has not been tested on iOS yet. **Stop** and **Discard** work while the microphone is released and do not need microphone permission.

### Closing Or Reloading The Tab

Closing or reloading the Nojoin tab pauses the recording. Audio the server already has stays there, and audio recorded but not yet uploaded is kept in this browser's storage for the Nojoin site. Only the unfinished last segment, under two seconds, is lost. Next time you open Nojoin, it asks you to resume, stop and process, or discard; resuming or stopping in the same browser uploads the kept audio first.

The pause can fail to arrive: the tab closed while signed out, or with no connection to the server. The recording then stays in progress with no tab capturing it. Once no audio has reached the server for two minutes, Nojoin offers it in the same dialog as an interrupted recording, in any tab or on any device, without a reload. Until then, **Start Meeting** reports that a recording is still in progress. A recording still open in another tab of the same browser is never offered, however long it has gone without uploading, since that tab may only be waiting for you to sign in again or for the connection to return.

If the tab was lost before any audio reached the server, Nojoin discards the empty recording and tells you to start again. The usual cause is a server upgrade while the tab was open: the first page change after **Start Meeting** reloads the app. Select **Start Meeting** again.

### Signing In Again During A Recording

A sign-in lasts 12 hours. If it ends while you record, because it expired or because you signed out in another tab, Nojoin keeps recording and asks for your password in a dialog on the same page. Going to the sign-in page would reload the tab and end the capture. Uploads wait meanwhile and the audio is kept in this browser. Once you sign in, it uploads and the recording carries on with no gap. A note from the [note box](#notes-during-capture) that had not saved waits too, and saves once you sign in.

Sign in as the account that started the recording. Signing in from another tab works as well: the recording tab notices within 15 seconds, or as soon as you return to it. While the browser is signed in as another account, the recording waits for its own account. If an administrator has reset your password, choose a new one in another tab, then sign in with it.

**Stop recording** in the dialog, or the browser's **Stop sharing** button, stops capturing and releases the microphone and shared tab at once. The rest of the audio uploads, and the recording is processed, after you sign in. Pause and discard need you to sign in first.

Keep the tab open until you have signed in. A tab closed or reloaded while signed out cannot pause the recording, so it stays in progress, and the audio it had not uploaded stays in this browser. Two minutes after its last upload, Nojoin offers it as an interrupted recording ([Closing Or Reloading The Tab](#closing-or-reloading-the-tab)). Resume it or choose **Stop and process** in the same browser to upload the kept audio first.

### Background Tabs And Sleep

If the browser or operating system suspends the Nojoin tab, capture receives no audio until it wakes, and that audio cannot be recovered. The recording does not pause and nothing reports it at the time. Nojoin notices afterwards by comparing the audio the server holds with the elapsed time, and warns you (see [Recorded audio is shorter than the meeting](#recorded-audio-is-shorter-than-the-meeting)).

While recording, Nojoin holds a screen wake lock so the display does not blank and the device does not idle to sleep. It cannot stop the browser suspending a tab, a laptop lid being closed, or the device being put to sleep by hand. Keep the Nojoin tab open and the device awake, and do not rely on a background tab for a long meeting.

## Notes During Capture

Notes typed under the live transcript ([Live Notes](USAGE.md#live-notes)) are placed on the recording's own clock, the audio Nojoin has received, not the wall clock. Paused time is not on it, so a note written while paused follows the last line recorded before the pause. A note usually lands a fraction of a second early, by the time a segment takes to upload, including in the first moments after **Resume**. When the recording device falls behind with its uploads, on a slow connection say, the clock waits near the audio received so far, and notes written meanwhile, on any device, land there.

To take notes on a second device, such as a phone while a laptop records, sign in there and open the recording from the recordings list. That device shows the live transcript and the note box too, places notes on the same clock, and each device shows the other's notes within a couple of seconds. Taking notes never affects what is captured.

The composer remains beneath **Transcript** after Stop. A carried draft keeps its text, attached file and displayed placement through uploading and processing; **At send** uses the stopped audio clock, or the recording's end if necessary. Changing **Place note** deliberately retargets it. A new blank draft after capture defaults to **Before the transcript**, with **After the transcript** also available. Notes that have not saved and files that have not uploaded retain **Retry** and **Discard**, without duplicating the composer's draft.

Use the camera button beside the composer or the screen-capture shortcut to take a still. One key press takes one still, including after changing tabs. Files still accept paste/drop into the composer, and saved files remain in **Captures**.

## Live Word Settling

[Live word settling](USAGE.md#live-transcription) defaults to **Auto (GPU)**, with **3 unchanged comparisons** and a **10 s settling window**, and revises only recent transcript words and speaker boundaries. Administrators can turn it off; a stored Off stays off on upgrade. Under **Auto (GPU)** CPU processing keeps ordinary whole-line transcription; **GPU and CPU** settles there too, at a high CPU and RAM cost. Pause and Stop lock the last accepted words without another decode, and the partial segment uploaded at Pause is still transcribed while paused, then locked; Resume starts a fresh settling episode; a capture that started with settling Off stays whole-line until it ends. This changes neither recorded audio nor upload cadence. When uploads fall behind, settling skips intermediate decoding passes while retaining the uploaded audio for final processing.

A live transcription pass that Pause interrupts still lands, and with settling its words lock while paused. A pass running when Resume starts a new settling episode, or when another device takes the capture over, is dropped instead, and the next upload after Resume drains its retained audio. Live text continues on the recording's audio clock, including a partial segment uploaded at Pause.

Settled lines follow continuous speech rather than the two-second upload steps. Words from the same person extend the open line until a speaker change, a pause longer than **Line break pause** (1 s by default), or the maximum line length (30 s by default). The pause setting is shared with diarised final transcripts under **Settings > Transcription > Transcript lines**, and the line cap is shared with ordinary live transcription. Earlier words can settle while their line continues growing. Pause and Stop close the remaining line.

How each step decides whether to transcribe again, from voice activity and speaker evidence, is in [Live Word Settling](ARCHITECTURE.md#live-word-settling).

## Naming Speakers During Capture

With live speaker labels on, a speaker's label in the live transcript can be renamed while the recording runs or is paused ([USAGE.md](USAGE.md#live-transcription)), from any device showing the live view. The name is kept with that label's speech, and processing gives it to the speaker who holds most of it, so it survives the processed transcript numbering speakers afresh. A name given before a pause lands as well as one given after it, since paused time is not on the recording's clock. Naming closes at **Stop**: a name sent as the recording stops is refused with a message, and the speaker is then renamed on the processed transcript. Naming never affects what is captured.

Live speech uses the same speaker-coloured cards as the finished transcript, with **Microphone** and **Shared audio** labels where the capture channel changes. Every line names its speaker, as finished lines do. A refused name or correction keeps its draft to retry; Stop closes any that are open. Scrolling away stops live following; **Jump to latest** follows new lines and in-place growth again. Play buttons remain visible but disabled until playback is available, and export has its own disabled control.

The **Speakers** tab also shows live labels, with **Change identity** and **Add to People**. An ordinary name can link an existing person; only **Add to People** explicitly creates one. Name the label first. This creates no live voiceprint, and final processing still checks the speaker identity and can ask for confirmation. If Stop has closed the recording, the action is refused without creating a person.

## Paused Recording Lock

Each user can run one browser capture at a time. While this tab is capturing, the microphone button in the collapsed recordings list reads **Meeting in progress**, and **Start Meeting** reads the same if the recorder fails while this tab still holds the capture. Pause or stop from the recording's page. While a paused recording exists, the **Start Meeting** button reads **Paused recording needs attention** and imports are blocked until you choose in the dialog. An interrupted recording, one whose tab closed without pausing it, holds the same lock, and the button reads **Interrupted recording needs attention**. A file import never holds it, even one that did not finish ([Importing Recordings](USAGE.md#importing-recordings)). The dialog offers:

- **Resume recording** continues the same recording with the source chosen beside **Start Meeting**. With **Microphone and a tab or screen**, the browser asks you to share again.
- **Stop and process** finalizes the audio already captured and queues processing without opening the share picker. Use it when the meeting is over or the capture was lost.
- **Discard recording** deletes the partial recording.

Nojoin keeps a paused or interrupted recording until you choose. It never pauses, cleans up or finalizes one for you. If the tab recording an interrupted capture was only cut off and starts uploading again, the dialog closes by itself within 30 seconds, and Nojoin refuses to resume that recording anywhere else while it is receiving audio.

Resuming a recording in another tab or on another device takes it over. If the tab that was recording it is still open and reaches the server again, it stops recording, releases the microphone and shared tab, and says the recording continued elsewhere. Pause, stop and discard in that tab are refused the same way, so it cannot end or delete the recording under the tab now running it. Audio it recorded but had not uploaded is left out rather than mixed into the audio recorded since, and Nojoin says how much and offers it as a download ([Audio Left Out Of A Recording](#audio-left-out-of-a-recording)). Audio a closed tab kept in its browser is treated the same way: it is uploaded when you resume or stop the recording in that browser only if nobody resumed it elsewhere after that tab, and otherwise left out. Once the recording has been stopped or finished somewhere else, audio a browser kept for it can never join it, so the next time Nojoin checks for unfinished recordings in that browser it offers that audio as a download too. Audio kept for a recording discarded somewhere else is removed from the browser instead.

**Stop and process** in another tab or on another device ends the recording for the tab that was recording it in the same way. Once that tab reaches the server again, it stops recording, releases the microphone and shared tab, and says the recording was already stopped and sent for processing. **Discard** in that tab is refused too, so it cannot delete the meeting being processed. To delete it, use **Discard Recording** in the recordings list.

**Discard recording** in another tab or on another device ends it for that tab too, which then says the recording was discarded. A request sent while the browser is signed in as another account, from another tab, also finds the recording missing, so the tab first checks which account the browser is signed in as. If it is someone else, the tab keeps the recording and asks its owner to sign in again ([Signing In Again During A Recording](#signing-in-again-during-a-recording)).

### Audio Left Out Of A Recording

When audio a browser recorded can never join its recording, Nojoin joins it into one FLAC file, keeps it in that browser and offers it in a notification that stays until you act on it. That happens when a capture is resumed or stopped in another tab or on another device before the tab recording it had uploaded everything, and when a recording the browser kept audio for is stopped or finished somewhere else.

- **Download** saves the file and removes the browser's copy.
- **Delete** removes it.
- Closing the notification hides it until you next open or reload a Nojoin page.

The file holds only the stretch missing from the recording, exactly as recorded: 16 kHz, with the shared audio on the left channel and the microphone on the right. To have it transcribed, import it as a recording of its own ([Importing Recordings](USAGE.md#importing-recordings)).

Nojoin offers the audio only while the browser is signed in as the account that recorded it. Audio nobody downloads or deletes is removed seven days after it was left out, and clearing the site's data removes it at once.

## Capture Settings

**Settings > Recording** holds the capture settings for this browser:

- **Microphone**, the input mixed with shared audio (**System default** unless you pick one).
- **Release the microphone while paused**, off by default. Frees the microphone after a pause so another application can use it; shared audio stays open. Resume opens the microphone again. A change applies at the next pause, including during a recording.
- **Microphone gain** and **Shared-audio gain**. Nojoin also balances the two levels automatically while recording.
- **Live microphone input test**, which shows raw and gain-adjusted levels.
- Under **Advanced**, the browser's echo cancellation, noise suppression and auto gain, and the **Quiet audio warnings** reset.

Noise suppression is off by default: in a room it makes speaker labels less accurate and doesn't improve the transcript. Turn it on for a noisy space. Echo cancellation and auto gain stay on. A browser that saved these settings before noise suppression defaulted off has it turned off once, because those saves stored the old default rather than a choice, and if you turn it back on it stays on.

These settings are stored in the browser, so they do not follow you to another browser or device.

If the microphone you chose is unavailable when a recording starts or resumes, Nojoin stops with an error instead of falling back to the system default. Pick another microphone first. The exception is **Resume** in the tab that released its microphone during the pause: if the chosen microphone is missing, Nojoin falls back to the system default microphone, shows a warning and keeps your saved choice. With the microphone released, a **Microphone** chosen in Settings while paused is used by the next Resume.

Each start and resume writes a `Capture source report` line to the API log with the requested and granted microphone and whether shared audio was granted. Browsers do not reveal which speaker or headset sits behind shared system audio.

## Troubleshooting

### No audio track in the shared stream

The browser shared the screen or tab without audio, and Nojoin is recording your microphone only. Start again with the picker's audio option on. If you shared a window, share the meeting tab instead.

### Live waveform is quiet

Check that the meeting is making sound, that the picker's audio option was on, and the gain in **Settings > Recording**. The input test there shows whether the microphone level is high enough. If only your microphone moves the waveform, shared audio was not granted.

After 20 seconds without sound, Nojoin shows an "Audio has been quiet for a bit" hint. **Don't show again** turns these hints off in this browser; turn them back on under **Settings > Recording**, **Advanced**, **Quiet audio warnings**.

### Meeting Edge is still empty during capture

Meeting Edge needs AI configured and enough completed speech to work from, so a short test can show waveform activity and live transcript lines before any guidance appears.

Open the **Meeting Edge** tab in the desktop side column or the phone's main tab row. It retains earlier guidance, questions, tracked-term history and focus after Stop, processing success or failure, and after an administrator switches the feature off. Focus and the recording-specific context slider become read-only after capture ends; any pending focus autosave is cancelled. Guidance can continue while uploading, queued or processing, and an update already running can finish while paused. Update status and errors show only during recording or a pause, and an update still running when you press Stop is dropped.

### The Transcript tab stays empty

Text appears a few seconds behind speech. If the waveform is flat too, the problem is capture, not transcription. Imported files have no browser capture feed; wait for upload and transcription to finish.

### Linux screen audio does not work

Use a recent Chromium-family browser in a desktop session with PipeWire screen capture. PulseAudio-only systems cannot capture screen or system audio.

### macOS shared audio does not work

Use current Chrome and current macOS, share the meeting tab and turn on the picker's audio option. If window or entire-screen audio is missing, use tab sharing. If you use another Chromium-family browser, try Chrome.

### The browser asks for permissions again on resume

This is expected after a reload, a closed tab or a failed stop. Browsers do not let a page reopen shared tab, window, screen or microphone capture without asking. With **Release the microphone while paused** on, a live resume may ask for microphone permission too; the shared tab or screen stays connected and needs no new share picker.

### Recorded audio is shorter than the meeting

Every 15 seconds Nojoin compares the audio the server holds with the elapsed time. When more than a minute is missing, and more than 2% of the meeting, the controls show the captured time next to the elapsed time and a notification names one of three causes. Nojoin warns again when you stop.

**The tab was suspended.** The browser or operating system suspended the Nojoin tab, or the device slept. That stretch was never recorded and cannot be recovered; the rest of the recording processes normally. To avoid it:

- Keep the Nojoin tab open, preferably visible in its own window.
- Keep the device awake for the whole meeting. Closing a laptop lid or putting the machine to sleep suspends capture.
- On a phone, keep Nojoin in the foreground and stop the phone locking.
- If it keeps happening in desktop Chrome, see [Chrome Memory Saver](#chrome-memory-saver).

**Nojoin could not reach the server.** Audio recorded meanwhile waits in the browser and uploads when the connection returns ([Uploads are behind](#uploads-are-behind)). If the tab closes first, reopen Nojoin in the same browser and resume or choose **Stop and process**. This is a server or network problem, so browser settings will not help. An outage stays named as the cause for five minutes after the server answers again, while the queue drains.

**No cause identified.** The warning gives only the shortfall. Check that the tab stayed open and the device awake, and that the connection to your Nojoin server is healthy.

Audio still queued in the browser is not counted as missing; the warning lists it separately. A shortfall that remains after the queue drains is lost audio. Dismissing the warning hides it until the shortfall grows by another minute.

### Chrome Memory Saver

Memory Saver puts inactive Chrome tabs to sleep. You should not normally need to change anything: Chrome exempts tabs that are using the microphone or sharing a screen. Desktop capture keeps any share open while paused; the microphone stays open too unless **Release the microphone while paused** is on. Keep Nojoin open and the device awake before resuming.

If shortfalls blamed on tab suspension keep happening in desktop Chrome, exempt Nojoin by hand:

1. Open **Settings > Performance > Memory Saver**.
2. Under **Always keep these sites active**, select **Add**.
3. Enter the address you use for Nojoin, for example `nojoin.example.com`.

No browser API lets a page opt out of Memory Saver or read whether it is on, so Nojoin cannot do this or check it for you. Chrome documents the feature at [Memory Saver in Chrome](https://support.google.com/chrome/answer/12929150).

### Uploads are behind

When more than 30 seconds of audio is waiting to upload, the controls say how far behind uploads are. The audio is kept in this browser and sent as the connection allows. Keep the tab open, though nothing is lost if it closes and you return in the same browser.

If a segment keeps failing to upload for about 15 seconds, Nojoin pauses the recording and shows the error. The tab keeps the queued audio and the shared tab. It keeps the microphone too unless **Release the microphone while paused** is on, in which case Resume opens it again and may ask for permission. **Start Meeting** stays unavailable until you resume, stop or discard. When the server cannot be told about the pause, Nojoin tells it once the connection returns, after uploading the queued audio, and **Resume** carries on with the same recording without asking you to share again. Two minutes after the last upload the server offers the recording as interrupted in any tab or on any device ([Closing Or Reloading The Tab](#closing-or-reloading-the-tab)). If it is resumed or stopped somewhere else meanwhile, this tab stops once the connection returns and says so ([This recording continued elsewhere](#this-recording-continued-elsewhere), [This recording has already been stopped](#this-recording-has-already-been-stopped)). An ended sign-in is different: recording carries on and uploads wait for you to sign in again ([Signing In Again During A Recording](#signing-in-again-during-a-recording)).

### This recording continued elsewhere

The recording was resumed in another tab or on another device, so this tab stopped recording it and released the microphone and shared tab. Carry on, pause or stop from the tab or device that resumed it. If this tab had audio it had not uploaded, the message says how much: it is not in the recording, because adding it after the audio recorded meanwhile would put the meeting out of order. Nojoin offers it as a download ([Audio Left Out Of A Recording](#audio-left-out-of-a-recording)).

### This recording was discarded elsewhere

The recording was discarded in another tab or on another device, so this tab stopped recording it and released the microphone and shared tab. Nothing of it is kept on the server. Audio this tab had not uploaded is not in any recording, and Nojoin removes it from this browser.

### This recording has already been stopped

The recording was stopped in another tab or on another device and sent for processing, so this tab stopped recording it and released the microphone and shared tab. The meeting appears in the recordings list as usual. If this tab had audio it had not uploaded, the message says how much: it is not in the recording, which was finished without it, but you can download it ([Audio Left Out Of A Recording](#audio-left-out-of-a-recording)).

### Stop did not finish

The meeting button and the recording header name the stage while stopping. The header keeps the upload backlog visible even if the server still reports recording or paused; its transport is disabled while Stop finishes. The transcript remains visible. If stopping fails, the recording returns to paused with an error, and Nojoin pauses it on the server too, once it can reach it. You can then stop it again from this tab's controls on the recording's page or from the global resume-or-discard dialog. Stopping does not need the original capture, so it works after a reload.

### Browser recording unavailable

The meeting button reads **Unsupported browser** when the browser offers no microphone access or no AudioWorklet support. Open Nojoin in a browser from [Supported Environments](#supported-environments). Firefox and Safari record the microphone only; for the meeting's shared audio, use Chrome or another Chromium-family browser on desktop.

If the browser is already supported, check the address. Browsers allow capture only on a secure origin, so a supported browser reaching Nojoin over plain HTTP on any address other than `localhost` reports as unsupported. See [HTTPS Is Required for Live Capture](DEPLOYMENT.md#https-is-required-for-live-capture).

## Privacy Notes

The browser decides what Nojoin receives: the microphone you allow and, in shared-audio mode, the tab, window or screen you pick. Firefox, Safari and phones always use the microphone only. To end capture, stop the meeting in Nojoin or, when sharing, stop sharing from the browser's indicator.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [USAGE.md](USAGE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [SECURITY.md](SECURITY.md)
