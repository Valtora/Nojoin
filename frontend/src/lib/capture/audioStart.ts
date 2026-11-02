import { AudioNotStartedError } from "./shared";

/**
 * How long a new audio context may take to start: a capture's at Start or at a
 * Resume after a reload, and the microphone input test's in Settings.
 *
 * Starting it usually takes a few milliseconds, but it opens the audio device,
 * and a Bluetooth headset that switches profile as its microphone opens can
 * take several seconds. A context the browser never starts would otherwise
 * leave Start or a Resume after a reload waiting for ever (issue #924), and the
 * input test spinning with the microphone open (issue #925).
 */
export const AUDIO_START_TIMEOUT_MS = 10_000;

/**
 * Asks the context to render, waiting at most `timeoutMs`. True once it is
 * running. A context the browser will not start, or an interrupted one (iOS
 * during a call), can leave `resume()` pending, so it is never awaited alone.
 */
export const resumeWithin = async (context: AudioContext, timeoutMs: number) => {
  if (context.state !== "running") {
    let timer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([
      context.resume().catch(() => {}),
      new Promise<void>((resolve) => { timer = setTimeout(resolve, timeoutMs); }),
    ]);
    clearTimeout(timer);
  }
  return context.state === "running";
};

/**
 * Starts a new context within `AUDIO_START_TIMEOUT_MS`. Past that, or when the
 * browser refuses, closes it and throws `AudioNotStartedError`.
 */
export const startAudioContext = async (context: AudioContext) => {
  if (await resumeWithin(context, AUDIO_START_TIMEOUT_MS)) {
    return;
  }
  // Not awaited: a browser that would not start the context may not close it promptly.
  void context.close().catch(() => {});
  throw new AudioNotStartedError();
};
