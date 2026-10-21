# Browser Capture Guide

Nojoin records live meetings in the browser. This guide covers which browsers can record, what to share, and what to do when audio goes missing.

## Supported Environments

| Browser | Live capture |
| --- | --- |
| Chrome on Windows, Linux or macOS | Shared audio and microphone |
| Edge, Brave, Arc or another Chromium-family browser on Windows or Linux | Shared audio and microphone |
| Other Chromium-family browsers on macOS | Shared audio and microphone, best-effort |
| Chrome on Android or iOS | Microphone only |
| Firefox, Safari, other mobile browsers | Not supported |

Shared-audio capture records the tab, window or screen you share plus your microphone. On Linux it needs PipeWire screen capture. Mobile browsers cannot share another tab's or app's audio, so a phone records only what its microphone hears.

On macOS, tab audio is the most reliable choice. Window and entire-screen audio work on current Chrome and current macOS when the share picker offers an audio toggle; older versions may share video with no audio. Other Chromium-family browsers on macOS are best-effort because their picker can lag Chrome's.

Browsers that cannot record can still play recordings, edit transcripts, manage speakers, search and administer Nojoin.

## What Nojoin Captures

Nojoin records two channels: the shared tab, window or screen audio, and your microphone. It uploads them as you record, in two-second lossless segments, using about 0.15 Mbit/s while one source carries sound and 0.3 Mbit/s while both do. The finished recording is a FLAC file exactly as long as the audio captured.

If you do not share tab audio, the shared channel is silent and everyone in the room is recorded through the microphone. On a phone, remote participants are recorded only if the phone's microphone can hear them.

While you record, the recording page shows a live waveform and a **Live Transcript** panel a few seconds behind the conversation ([USAGE.md](USAGE.md#live-transcription)).

A segment the server cannot decode is set aside and leaves a two-second gap instead of stopping the meeting. The API logs it as a `segment_corrupt_skipped` pipeline metric.

## Starting A Recording

1. On the dashboard, select **Start Meeting**.
2. On desktop, choose the meeting tab, window or entire screen in the browser's share picker, turn on its audio option if it offers one, and select **Share**.
3. Allow microphone access if asked.
4. Keep the Nojoin tab open and the device awake until you stop.

The browser may switch to the tab or window you shared; Nojoin keeps recording in its own tab. Selecting **Cancel** in the picker returns you to where you were with no recording started. Before relying on Nojoin for an important meeting, record a short test and check that the live waveform moves.

A phone shows no share picker. Allow microphone access, keep the phone near the meeting audio, keep Nojoin in the foreground and stop the phone locking.

## Choosing Tab, Window, Or Entire Screen

- **Browser tab** suits Google Meet, Microsoft Teams, Zoom, Webex and other web meeting apps. Chrome and Edge offer **Share tab audio**. Some enterprise sign-in or protected meeting pages need you to share a different tab once sign-in completes.
- **Window** can work, but some browsers and operating systems give no audio for a window. If Nojoin then warns that no system or tab audio was detected, share the meeting tab instead.
- **Entire screen** suits a native meeting app or audio from several windows. On Windows, turn on **Also share system audio**. On Linux, screen audio depends on the browser, the desktop environment and PipeWire. On macOS, see [Supported Environments](#supported-environments).

Brave may need its shields or site settings relaxed before it shows capture prompts on a hardened profile. If **Share** is disabled, select a source first and check that any required audio toggle is on.

## Pause, Resume, Stop, And Discard

- **Pause** stops capturing and keeps everything recorded so far.
- **Resume** continues the same recording. After a pause in the live controls it carries on with the same shared tab and microphone. After the tab was closed or reloaded, the browser asks you to share again.
- **Stop** uploads any audio still queued, releases the microphone and shared tab, and queues the recording for processing. While queued audio uploads, the controls read **Uploading** and show how much is left, and the meeting button names the current stage. Stop also works on a paused recording, including one whose tab was closed.
- **Discard** permanently deletes an unfinished recording, whether it is recording, paused, queued or processing: its audio, any attached documents, any processing under way and the meeting itself. Nojoin asks you to confirm first. Discard is in the live controls, the floating recording badge, the resume-or-discard dialog and the recordings list's context menu (**Discard Recording**).

Selecting the browser's own **Stop sharing** button, or closing its sharing indicator, stops and saves the recording with everything captured so far, and Nojoin tells you it did.

Switching tabs or windows, using another application or moving between Nojoin pages does not pause recording. On every page except the recording itself, a floating badge at the top of the screen shows the status and elapsed time, with pause, resume, stop and discard controls, and opens the recording when clicked.

### Closing Or Reloading The Tab

Closing or reloading the Nojoin tab pauses the recording. Audio the server already has stays there, and audio recorded but not yet uploaded is kept in this browser's storage for the Nojoin site. Only the unfinished last segment, under two seconds, is lost. Next time you open Nojoin, it asks you to resume, stop and process, or discard; resuming or stopping in the same browser uploads the kept audio first.

If the tab was lost before any audio reached the server, Nojoin discards the empty recording and tells you to start again. The usual cause is a server upgrade while the tab was open: the first page change after **Start Meeting** reloads the app. Select **Start Meeting** again.

### Background Tabs And Sleep

If the browser or operating system suspends the Nojoin tab, capture receives no audio until it wakes, and that audio cannot be recovered. The recording does not pause and nothing reports it at the time. Nojoin notices afterwards by comparing the audio the server holds with the elapsed time, and warns you (see [Recorded audio is shorter than the meeting](#recorded-audio-is-shorter-than-the-meeting)).

While recording, Nojoin holds a screen wake lock so the display does not blank and the device does not idle to sleep. It cannot stop the browser suspending a tab, a laptop lid being closed, or the device being put to sleep by hand. Keep the Nojoin tab open and the device awake, and do not rely on a background tab for a long meeting.

## Paused Recording Lock

Each user can run one browser capture at a time. While a paused recording exists, the **Start Meeting** button reads **Paused recording needs attention** and imports are blocked until you choose in the dialog:

- **Resume recording** continues the same recording. The browser asks you to share again.
- **Stop and process** finalizes the audio already captured and queues processing without opening the share picker. Use it when the meeting is over or the capture was lost.
- **Discard recording** deletes the partial recording.

Nojoin keeps a paused recording until you choose. It never cleans one up or finalizes it for you.

## Capture Settings

**Settings > Recording** holds the capture settings for this browser:

- **Microphone**, the input mixed with shared audio (**System default** unless you pick one).
- **Microphone gain** and **Shared-audio gain**. Nojoin also balances the two levels automatically while recording.
- **Live microphone input test**, which shows raw and gain-adjusted levels.
- Under **Advanced**, the browser's echo cancellation, noise suppression and auto gain, and the **Quiet audio warnings** reset.

These settings are stored in the browser, so they do not follow you to another browser or device.

If the microphone you chose is unavailable when a recording starts or resumes, Nojoin stops with an error instead of falling back to the system default. Pick another microphone first.

Each start and resume writes a `Capture source report` line to the API log with the requested and granted microphone and whether shared audio was granted. Browsers do not reveal which speaker or headset sits behind shared system audio.

## Troubleshooting

### No audio track in the shared stream

The browser shared the screen or tab without audio, and Nojoin is recording your microphone only. Start again with the picker's audio option on. If you shared a window, share the meeting tab instead.

### Live waveform is quiet

Check that the meeting is making sound, that the picker's audio option was on, and the gain in **Settings > Recording**. The input test there shows whether the microphone level is high enough. If only your microphone moves the waveform, shared audio was not granted.

After 20 seconds without sound, Nojoin shows an "Audio has been quiet for a bit" hint. **Don't show again** turns these hints off in this browser; turn them back on under **Settings > Recording**, **Advanced**, **Quiet audio warnings**.

### Meeting Edge is still empty during capture

Meeting Edge needs AI configured and enough completed speech to work from, so a short test can show waveform activity and live transcript lines before any guidance appears.

### The Live Transcript panel stays empty

Text appears a few seconds behind speech. If the waveform is flat too, the problem is capture, not transcription. The panel is always empty for an imported file, which is transcribed after upload.

### Linux screen audio does not work

Use a recent Chromium-family browser in a desktop session with PipeWire screen capture. PulseAudio-only systems cannot capture screen or system audio.

### macOS shared audio does not work

Use current Chrome and current macOS, share the meeting tab and turn on the picker's audio option. If window or entire-screen audio is missing, use tab sharing. If you use another Chromium-family browser, try Chrome.

### The browser asks for permissions again on resume

This is expected after a reload, a closed tab or a failed stop. Browsers do not let a page reopen shared tab, window, screen or microphone capture without asking.

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

Memory Saver puts inactive Chrome tabs to sleep. You should not normally need to change anything: Chrome exempts tabs that are using the microphone or sharing a screen, and a recording tab does both until you stop.

If shortfalls blamed on tab suspension keep happening in desktop Chrome, exempt Nojoin by hand:

1. Open **Settings > Performance > Memory Saver**.
2. Under **Always keep these sites active**, select **Add**.
3. Enter the address you use for Nojoin, for example `nojoin.example.com`.

No browser API lets a page opt out of Memory Saver or read whether it is on, so Nojoin cannot do this or check it for you. Chrome documents the feature at [Memory Saver in Chrome](https://support.google.com/chrome/answer/12929150).

### Uploads are behind

When more than 30 seconds of audio is waiting to upload, the controls say how far behind uploads are. The audio is kept in this browser and sent as the connection allows. Keep the tab open, though nothing is lost if it closes and you return in the same browser.

If a segment keeps failing to upload for about 15 seconds, Nojoin pauses the recording and shows the error. The queued audio is kept, and resuming or stopping uploads it first.

### Stop did not finish

The meeting button names the stage while stopping. If stopping fails, the recording returns to paused with an error, and you can stop it again from the live controls, the floating badge or the resume-or-discard dialog. Stopping does not need the original capture, so it works after a reload.

### Browser recording unavailable

The meeting button reads **Unsupported browser**. Open Nojoin in a browser from [Supported Environments](#supported-environments).

If the browser is already supported, check the address. Browsers allow capture only on a secure origin, so a supported browser reaching Nojoin over plain HTTP on any address other than `localhost` reports as unsupported. See [HTTPS Is Required for Live Capture](DEPLOYMENT.md#https-is-required-for-live-capture).

## Privacy Notes

The browser decides what Nojoin receives: on desktop, the tab, window or screen you pick and the microphone you allow; on a phone, the microphone only. To end capture, stop the meeting in Nojoin or stop sharing from the browser's indicator.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [USAGE.md](USAGE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [SECURITY.md](SECURITY.md)
