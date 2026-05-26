# Browser Capture Guide

Nojoin now records meetings directly from the browser capture stack.

## Supported environments

- Chrome, Edge, Brave, and Arc on Windows
- Chrome, Edge, Brave, and Arc on Linux

## Unsupported environments

- Firefox
- Safari
- Mobile browsers
- Chromium browsers on macOS

## Operator notes

- The browser share picker must include tab or system audio for remote participants to be captured.
- The microphone device and gain controls are stored in browser-local storage for this cutover.
- A paused recording blocks new capture starts until the user resumes or discards it.

## User flow

1. Open Meet Now.
2. Choose the meeting tab, window, or screen in the browser share picker.
3. Turn on system or tab audio if the picker offers it.
4. Record, pause, resume, and stop from the Nojoin UI.

## Troubleshooting

- If browser recording is unavailable, switch to a supported Chromium browser on Windows or Linux.
- If the microphone list is blank, allow microphone access once and refresh the device list in settings.
- If the waveform is quiet, adjust the capture gain sliders and retry with a short test meeting.