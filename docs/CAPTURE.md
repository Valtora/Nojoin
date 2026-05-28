# Browser Capture Guide

Nojoin records live meetings directly from the web app using browser capture APIs. There is no desktop helper or native tray process in the browser-only release.

Use this guide when you are preparing a browser for live recording, choosing what to share, troubleshooting missing audio, or recovering a paused recording.

## Supported Environments

Live capture is supported only in Chromium-family browsers on Windows and Linux:

| Browser or OS | Capture support |
| --- | --- |
| Chrome on Windows | Supported |
| Edge on Windows | Supported |
| Brave on Windows | Supported |
| Arc on Windows | Supported |
| Chrome, Edge, Brave, or Arc on Linux with PipeWire screen capture | Supported |
| Firefox | Not supported for live capture |
| Safari | Not supported for live capture |
| Mobile browsers | Not supported for live capture |
| Chromium browsers on macOS | Not supported for live capture |

Unsupported browsers can still review recordings, play audio, edit transcripts, manage speakers, use search, and administer Nojoin. They cannot start live capture.

## What Nojoin Captures

Nojoin combines two browser-granted audio sources:

- **Shared tab, window, or screen audio** for meeting participants and other system output.
- **Microphone audio** for the local speaker.

The browser mixes those sources in the Nojoin tab, uploads short WebM/Opus segments to the backend, and the worker transcodes each segment to the canonical 16 kHz, two-channel WAV path used by live transcription and final processing. Channel 0 carries shared/system audio and channel 1 carries microphone audio; speech recognition can use a mono mix derived from those preserved channels.

For support and debugging, browser recording segments are numbered from `0` and resume with the next sequence after the last uploaded segment. Finalization rejects missing sequence gaps. Live ASR and rolling speaker-window diarization are tracked separately in the backend, so a recording can have transcript coverage before every speaker-window pass has completed. Final processing reuses live text and speaker decisions only when they align by stable utterance id or clear time overlap; ambiguous spans keep the final pipeline output.

## Before Your First Recording

1. Open Nojoin in Chrome, Edge, Brave, or Arc on Windows or Linux.
2. Confirm your meeting platform is open in a browser tab if you want reliable tab audio capture.
3. Check that your microphone is available to the browser.
4. Open **Settings > Capture** if you need to choose a microphone or adjust system and microphone gain.
5. Start a short test meeting and verify that the live waveform responds before relying on Nojoin for an important meeting. If AI is configured, Meeting Edge guidance should begin updating once enough speech has accumulated.

## Starting A Recording

1. Open the Nojoin dashboard.
2. Select **Start Meeting**.
3. When the browser share picker opens, choose the meeting tab, application window, or entire screen you want Nojoin to hear.
4. Enable the browser's audio-sharing option before selecting **Share**.
5. Allow microphone access if prompted.
6. Keep the Nojoin tab open while recording.

Chrome and Edge may focus the chosen tab, window, or screen after you select **Share**. That is normal. You can continue using your computer, switch windows, or interact with the meeting while Nojoin keeps recording in the original browser tab.

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

## Browser Picker Audio Options

The exact wording is browser-dependent:

- Chrome on Windows usually shows **Also share system audio** for entire-screen sharing and **Share tab audio** for tab sharing.
- Edge on Windows follows the same general behavior as Chrome.
- Brave may require shields or site settings to allow capture prompts on hardened profiles.
- Linux Chromium builds require working desktop capture through PipeWire for screen capture. PulseAudio-only environments are expected to fail for system or screen audio.

If the share button is disabled, select a source first and confirm that any required audio toggle is enabled.

## Pause, Resume, Stop, And Cancel

- **Pause** keeps uploaded segments and stops new segment capture until you resume.
- **Resume** reopens the browser share picker and continues with the next 0-based segment sequence.
- **Stop** finalizes the recording after all uploaded segments finish transcoding, then queues final processing.
- **Cancel** discards an uploading or paused recording and clears the capture lock.

Refreshing, closing, or navigating away from the Nojoin tab during a recording moves that recording to `PAUSED`. Nojoin keeps uploaded segments, drops only the in-memory tail, and shows a mandatory resume-or-discard modal the next time you open the app.

Switching to another browser tab, changing the active window, or using another application does not pause recording. The Nojoin tab only pauses automatically when it is actually unloaded or when you intentionally navigate away inside the Nojoin app.

## Paused Recording Lock

Nojoin allows one active browser capture per user. If a paused recording exists, new capture and import entry points stay blocked until you choose one of the modal actions:

- **Resume recording** to continue capture from the same recording.
- **Discard recording** to remove the partial upload and start fresh.

Paused recordings are retained indefinitely. They are not cleaned up automatically.

## Capture Settings

Open **Settings > Capture** to configure:

- Microphone device.
- Shared audio gain.
- Microphone gain.

These settings are stored in browser-local storage for the current cutover. They do not roam between browsers or devices.

Nojoin intentionally does not include a settings-page capture preview. Validate capture by recording a short test meeting.

## Troubleshooting

### No audio track in the shared stream

The browser granted screen or tab visibility but no audio track. Start again and make sure the audio sharing checkbox is enabled. If you shared a window, try sharing the meeting browser tab instead.

### Live waveform is quiet

Check that the meeting is producing audible sound, verify the browser share picker audio option, then adjust gain in **Settings > Capture**. If only your microphone appears, the shared tab/window/screen audio was not granted.

### Meeting Edge is still empty during capture

Confirm the waveform shows speech activity. Meeting Edge only updates after Nojoin has enough completed speech to build guidance, so very short tests may show waveform activity before any live guidance appears. The user-facing transcript is expected to appear after final processing rather than during capture.

### Linux screen audio does not work

Use a recent Chromium-family browser and a desktop session with PipeWire screen capture. PulseAudio-only screen capture is not supported for reliable system audio.

### The browser asks for permissions again on resume

That is expected. Browsers do not let Nojoin silently recreate shared tab, window, or screen capture after a reload, close, or pause that released the previous tracks.

### Nojoin says a paused recording exists

Use the resume-or-discard modal. Starting a second recording while a paused upload exists is intentionally blocked to avoid overlapping segment streams and accidental data loss.

### Unsupported browser notice appears

Open Nojoin in a supported Chromium browser on Windows or Linux. Review and playback still work in unsupported browsers, but live recording does not.

## Privacy Notes

The browser picker controls what Nojoin can receive. Nojoin cannot silently capture your screen, tab, system audio, or microphone without browser permission. Stop sharing from the browser's sharing indicator or stop the meeting in Nojoin to end capture.

## Related Docs

- [GETTING_STARTED.md](GETTING_STARTED.md)
- [USAGE.md](USAGE.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [SECURITY.md](SECURITY.md)