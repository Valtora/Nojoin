# Browser Capture Guide

Nojoin records live meetings directly from the web app using browser capture APIs. There is no desktop helper or native tray process in the browser-only release.

Use this guide when you are preparing a browser for live recording, choosing what to share, troubleshooting missing audio, or recovering a paused recording.

## Supported Environments

Live capture has two browser modes:

- **Shared-audio capture** on Chrome for Windows, Linux, and macOS, and on Chromium-family browsers for Windows and Linux. Other Chromium-family browsers on macOS are best-effort. This captures the shared tab, window, or screen audio plus microphone audio when the browser grants a shared-audio track.
- **Microphone-only capture** on Chrome for Android and iOS. This records the phone microphone only; mobile browsers do not expose the shared tab, app, or system audio that Nojoin uses on desktop.

| Browser or OS | Capture support |
| --- | --- |
| Chrome on Windows | Shared audio + microphone |
| Edge on Windows | Shared audio + microphone |
| Brave on Windows | Shared audio + microphone |
| Arc on Windows | Shared audio + microphone |
| Chrome, Edge, Brave, or Arc on Linux with PipeWire screen capture | Shared audio + microphone |
| Chrome on macOS | Shared audio + microphone |
| Other Chromium browsers on macOS | Best-effort shared audio + microphone |
| Chrome on Android | Microphone-only |
| Chrome on iOS | Microphone-only |
| Firefox | Not supported for live capture |
| Safari | Not supported for live capture |
| Other mobile browsers | Not supported for live capture |

Unsupported browsers can still review recordings, play audio, edit transcripts, manage speakers, use search, and administer Nojoin. They cannot start live capture.

Chrome on macOS is a supported desktop capture path. Tab audio is the most reliable macOS option. Window and entire-screen audio should work on current Chrome and current macOS when the browser picker exposes and grants the audio toggle, but older browser or OS versions may return video without a shared-audio track. Other Chromium-family browsers on macOS are allowed by Nojoin but treated as best-effort because their picker behaviour can lag Chrome.

## What Nojoin Captures

Nojoin combines two browser-granted audio sources:

- **Shared tab, window, or screen audio** for meeting participants and other system output.
- **Microphone audio** for the local speaker.

On desktop shared-audio capture, the browser mixes those sources in the Nojoin tab, uploads short audio segments to the backend, and the worker transcodes each segment to the canonical 16 kHz, two-channel WAV path used by live transcription and final processing. Channel 0 carries shared/system audio when available and channel 1 carries microphone audio; speech recognition can use a mono mix derived from those preserved channels.

The recording page shows those transcribed segments in a **Live Transcript** panel as each sentence finalizes, a few seconds behind the conversation. Where one channel clearly carried a line, the panel labels it **Microphone** or **Shared audio**. Those labels describe the audio source and not the speaker: the capture always produces two channels, so if you do not share tab audio, channel 0 is silent and everyone in the room arrives on the microphone channel. Speaker names appear only after the recording is processed.

On mobile Chrome, Nojoin records only the phone microphone. The browser still uploads live segments into the same backend pipeline, but remote participants are captured only if the phone microphone can hear them from the room or device speaker. Keep the Nojoin tab open and the phone awake while recording.

For support and debugging, browser recording segments are numbered from `0` and resume with the next sequence after the last uploaded segment. Finalization rejects missing sequence gaps while uploads are still in flight, but a segment that repeatedly fails transcoding (for example one truncated by a network outage) is quarantined and skipped so finalization can complete with a short audio gap instead of failing forever; each skipped segment is logged as a `segment_corrupt_skipped` pipeline metric. Live ASR and rolling speaker-window diarisation are tracked separately in the backend, so a recording can have transcript coverage before every speaker-window pass has completed. Final processing reuses live text and speaker decisions only when they align by stable utterance id or clear time overlap; ambiguous spans keep the final pipeline output.

## Before Your First Recording

1. Open Nojoin in Chrome on Windows, Linux, or macOS for shared-audio capture, in Chrome, Edge, Brave, or Arc on Windows or Linux, or in Chrome on Android/iOS for microphone-only capture. Other Chromium-family browsers on macOS are best-effort.
2. Confirm your meeting platform is open in a browser tab if you want reliable tab audio capture.
3. Check that your microphone is available to the browser.
4. Open **Settings > Recording** if you need to choose a microphone or adjust system and microphone gain.
5. Start a short test meeting and verify that the live waveform responds before relying on Nojoin for an important meeting. If AI is configured, Meeting Edge guidance should begin updating once enough speech has accumulated.

## Starting A Recording

1. Open the Nojoin dashboard.
2. Select **Start Meeting**.
3. On desktop, when the browser share picker opens, choose the meeting tab, application window, or entire screen you want Nojoin to hear.
4. On desktop, enable the browser's audio-sharing or system-audio option when the picker offers one, then select **Share**.
5. Allow microphone access if prompted.
6. On mobile Chrome, keep the phone close enough for the microphone to hear the meeting audio.
7. Keep the Nojoin tab open and the device awake while recording.

Chrome and Edge may focus the chosen tab, window, or screen after you select **Share**. That is normal. You can continue using your computer, switch windows, or interact with the meeting while Nojoin keeps recording in the original browser tab.

If you close the browser share picker with **Cancel** instead of **Share**, Nojoin silently returns to the pre-start state and no recording is started.

Mobile Chrome does not show a share picker for Nojoin because the mobile path is microphone-only.

## Choosing Tab, Window, Or Entire Screen

### Browser Tab

Tab sharing is the preferred option for Google Meet, Microsoft Teams, Zoom, Webex, and other web meeting apps.

- It is the most reliable way to capture meeting audio.
- It usually exposes a clear `Share tab audio` option.
- It avoids accidentally showing unrelated windows to the browser capture session.
- Enterprise sign-in pages or protected meeting surfaces may occasionally require you to share a different tab after sign-in completes.

### Window

Window sharing can work, but some Chromium builds and operating systems do not provide window audio. If Nojoin warns that no shared audio track is present, share the meeting browser tab instead.

### Entire Screen

Entire-screen sharing is useful when the meeting audio comes from a native app or from multiple windows. On Windows, enable **Also share system audio** in the picker. On Linux, entire-screen audio depends on the browser, desktop environment, and PipeWire support.

On macOS, entire-screen and window audio should work on current Chrome and current macOS when Chrome offers an audio toggle in the picker. If the toggle is absent or Nojoin warns that no shared-audio track was granted, update Chrome and macOS, then retry with tab sharing.

## Browser Picker Audio Options

The exact wording is browser-dependent:

- Chrome on Windows usually shows **Also share system audio** for entire-screen sharing and **Share tab audio** for tab sharing.
- Edge on Windows follows the same general behaviour as Chrome.
- Chrome on macOS usually offers reliable tab audio. Window and entire-screen audio should work on latest Chrome and macOS when the picker shows the audio toggle.
- Brave may require shields or site settings to allow capture prompts on hardened profiles.
- Linux Chromium builds require working desktop capture through PipeWire for screen capture. PulseAudio-only environments are expected to fail for system or screen audio.

If the share button is disabled, select a source first and confirm that any required audio toggle is enabled.

## Mobile Chrome Microphone Recording

Chrome on Android and iOS can start recording from the same **Start Meeting** button, but it records only the phone microphone. It does not capture another mobile app, browser tab, headset output, or system audio. For best results, keep the meeting audio audible to the phone microphone, keep Nojoin visible, and prevent the phone from locking.

## Pause, Resume, Stop, And Discard

- **Pause** keeps uploaded segments and stops new segment capture until you resume.
- **Resume** reopens the browser share picker and continues with the next 0-based segment sequence.
- **Stop** finalizes the recording after all uploaded segments finish transcoding, then queues final processing. The microphone and any shared tab or screen are released as soon as the recorded audio has been handed off, so the browser recording indicator disappears while Nojoin waits for the last segments to be processed; the meeting button names the current stage during that wait (stopping the recorder, uploading the last segments, releasing the microphone, finalizing). Stop works on a paused recording too, and does not need the original browser capture session to still be active.
- **Discard** permanently removes a recording in one step. It works for any recording that has not finished: uploading, paused, queued, or processing. Discard revokes any running processing task, releases the capture lock, deletes the captured audio and derived files, and removes the meeting. Nojoin asks you to confirm first because this cannot be undone.

Discard is available from the live recording controls, the floating recording badge, the resume-or-discard modal, and the recordings menu.

Closing the browser share picker with **Cancel** is different from Nojoin's in-app **Discard** action. Picker cancel simply backs out of starting or resuming capture without creating a visible error in the UI, and never deletes an existing recording.

If you are recording in a shared-audio capture mode and click the browser's native **Stop sharing** button (or close the sharing indicator), Nojoin automatically detects that the sharing stream has ended. It will immediately stop and finalise the recording, saving all audio captured up to that point, and display a notification informing you that screen sharing ended and the recording was saved.

Refreshing or closing the Nojoin tab (actual tab unload) during a recording moves that recording to `PAUSED`. Nojoin keeps uploaded segments, drops only the in-memory tail, and shows a mandatory resume-or-discard modal the next time you open the app.

Switching to another browser tab, changing the active window, using another application, or navigating between pages within the Nojoin app does not pause recording. The Nojoin tab only pauses automatically when it is actually unloaded. A floating recording badge remains visible at the top of the viewport on every page so you can always see the recording status and control it.

Not pausing is not the same as being safe in the background. If the browser or the operating system suspends the Nojoin tab, browser capture stops receiving audio for as long as the suspension lasts, and that audio cannot be recovered afterwards. The recording is not paused and no error is raised, because nothing in a web page can prevent or undo being suspended. Nojoin detects the shortfall by comparing the audio it has stored against elapsed recording time, and shows a warning naming how much was captured. Keep the Nojoin tab open, keep the device awake, and do not rely on a background tab for a long meeting. See [Recorded audio is shorter than the meeting](#recorded-audio-is-shorter-than-the-meeting).

## Floating Recording Badge

While a recording is active, a floating badge appears at the top-centre of the viewport. The badge shows:

- A pulsing red dot and the word **Recording** (or **Paused** when paused).
- The elapsed recording time.
- Pause/resume and stop controls.

Clicking the badge navigates directly to the recording detail page. The badge is hidden on the recording detail page itself to avoid duplication. You can pause, resume, or stop the recording from any page without navigating back to the recording workspace first.

## Paused Recording Lock

Nojoin allows one active browser capture per user. If a paused recording exists, new capture and import entry points stay blocked until you choose one of the modal actions:

- **Resume recording** to continue capture from the same recording.
- **Stop and process** to finalize the audio already uploaded and queue processing, without recording any more. Use this when the meeting is over, or when the browser capture session was lost and you only want to keep what was captured. It does not reopen the share picker.
- **Discard recording** to remove the partial upload and start fresh.

Paused recordings are retained indefinitely. They are not cleaned up automatically, and Nojoin never finalizes one on your behalf.

## Capture Settings

Open **Settings > Recording** to configure:

- Microphone device.
- Shared audio gain.
- Microphone gain.
- Browser echo cancellation.
- Browser noise suppression.
- Browser automatic gain control.

The Capture settings page also includes a local microphone input test so you can watch raw and gain-adjusted levels in real time before recording.

These settings are stored in browser-local storage for the current cutover. They do not roam between browsers or devices.

If you explicitly choose a microphone and that device is no longer available when a recording starts or resumes, Nojoin now fails closed. It does not silently fall back to the system default microphone. Choose another device in **Settings > Recording** before retrying.

Nojoin logs the requested microphone, the browser-granted microphone track metadata, whether shared audio was granted, and the backend browser-live source-channel analysis. Browser APIs still do not reliably expose the physical OS speaker or headset name behind shared system audio, so those logs describe the granted browser capture surface and track metadata rather than a guaranteed output-device identity.

Nojoin intentionally does not include a settings-page capture preview. Validate capture by recording a short test meeting.

## Troubleshooting

### No audio track in the shared stream

The browser granted screen or tab visibility but no audio track. Start again and make sure the audio sharing checkbox is enabled. If you shared a window, try sharing the meeting browser tab instead.

### Live waveform is quiet

Check that the meeting is producing audible sound, verify the browser share picker audio option, then adjust gain in **Settings > Recording**. Use the local microphone input test to verify mic lift before recording. If only your microphone appears, the shared tab/window/screen audio was not granted.

### Meeting Edge is still empty during capture

Confirm the waveform shows speech activity. Meeting Edge only updates after Nojoin has enough completed speech to build guidance, so very short tests may show waveform activity before any live guidance appears. The Live Transcript panel fills sooner than Meeting Edge does, so text appearing there while Meeting Edge is still empty is expected rather than a fault.

### The Live Transcript panel stays empty

Text appears a few seconds behind speech, so allow for that before treating an empty panel as broken. Confirm the waveform is responding to audio: if it is flat, the problem is capture rather than transcription. The panel is also empty by design for an imported file, which is transcribed in a single pass after upload rather than by the live lane.

### Linux screen audio does not work

Use a recent Chromium-family browser and a desktop session with PipeWire screen capture. PulseAudio-only screen capture is not supported for reliable system audio.

### macOS shared audio does not work

Use current Chrome and current macOS, then choose the meeting tab and enable the audio option in the picker. If window or entire-screen audio is missing, retry with tab sharing first. Other Chromium-family browsers on macOS are best-effort, so switch to Chrome when troubleshooting.

### The browser asks for permissions again on resume

That is expected. Browsers do not let Nojoin silently recreate shared tab, window, screen, or microphone capture after a reload, close, or pause that released the previous tracks.

### Recorded audio is shorter than the meeting

The browser or the operating system suspended the Nojoin tab during capture. A suspended tab stops feeding audio to the recorder, so that stretch of the meeting was never recorded and cannot be recovered. The recording itself is intact and processes normally; it is only shorter than the elapsed time.

Nojoin warns while this is happening, showing the captured duration next to the elapsed timer, and warns again when the recording is stopped. To avoid it:

- Keep the Nojoin tab open, and prefer keeping it visible in its own window.
- Prevent the device from sleeping for the length of the meeting. Closing a laptop lid or letting the machine enter standby suspends capture.
- Exempt Nojoin from browser tab-suspension features. In Chrome, add the site to the exceptions under **Settings > Performance > Memory Saver**.
- On mobile Chrome, keep Nojoin in the foreground and stop the phone locking.

### Stop did not finish

The meeting button names the stage it is on while stopping. If stopping fails, the recording returns to paused with an error rather than being left in an unusable state, and you can stop it again from the live controls, the floating badge, or the resume-or-discard modal. Stopping does not need the original capture session, so it still works after a reload or after the browser has released the shared audio and microphone tracks.

### Nojoin says a paused recording exists

Use the modal. Resume to keep recording, **Stop and process** to keep what was already captured, or discard to remove the partial upload. Starting a second recording while a paused upload exists is intentionally blocked to avoid overlapping segment streams and accidental data loss.

### Unsupported browser notice appears

Open Nojoin in Chrome on desktop for shared-audio recording, another supported desktop Chromium browser, or Chrome on Android/iOS for microphone-only recording. Review and playback still work in unsupported browsers, but live recording does not.

If the browser is already a supported one, check the address you opened. Browsers expose capture APIs only on a secure context, so a supported browser reaching Nojoin over plain HTTP on any address other than `localhost` reports as unsupported. See [HTTPS Is Required for Live Capture](DEPLOYMENT.md#https-is-required-for-live-capture).

## Privacy Notes

The browser picker controls what Nojoin can receive on desktop. Nojoin cannot silently capture your screen, tab, system audio, or microphone without browser permission. On mobile Chrome, Nojoin receives only the microphone stream granted by the browser. Stop sharing from the browser's sharing indicator or stop the meeting in Nojoin to end capture.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [USAGE.md](USAGE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [SECURITY.md](SECURITY.md)
