/** Microphone-only ownership changes; the capture graph and upload queue survive. */
import type { CaptureMixer } from "./mixer";
import type { PickedCaptureSources } from "./pickSource";
import { reopenMicrophone } from "./reopenMicrophone";
import type { CaptureSettings } from "./shared";
import { describeCaptureSettings, describeTrack, type CaptureSourceReportSnapshot } from "./sourceReport";

export interface PausedMicrophoneRuntime {
  sources: PickedCaptureSources;
  mixer: CaptureMixer;
  captureReport: CaptureSourceReportSnapshot;
  microphoneReleased?: boolean;
  microphoneDeviceId?: string | null;
}

/** Call only after the recorder has flushed its pause tail. */
export const releasePausedMicrophone = (runtime: PausedMicrophoneRuntime, enabled: boolean) => {
  if (!enabled || runtime.microphoneReleased) {
    return;
  }
  // Retain this capture's input until another microphone is explicitly selected.
  if (runtime.microphoneDeviceId === undefined) {
    runtime.microphoneDeviceId = runtime.captureReport.requested_microphone_device_id ??
      runtime.sources.microphoneStream.getAudioTracks()[0]?.getSettings?.().deviceId ?? null;
  }
  runtime.mixer.detachMicrophone();
  runtime.sources.microphoneStream.getTracks().forEach((track) => track.stop());
  runtime.microphoneReleased = true;
};

export const assertMicrophoneResumeCurrent = (isCurrent: () => boolean) => {
  if (!isCurrent()) {
    throw new Error("The recording changed while the microphone was opening. Try Resume again if it is still paused.");
  }
};

/** Adopt a fresh input only while the same paused capture still owns this attempt. */
export const reconnectPausedMicrophone = async (
  runtime: PausedMicrophoneRuntime,
  settings: CaptureSettings,
  isCurrent: () => boolean,
): Promise<boolean> => {
  const { stream, usedDefault } = await reopenMicrophone({
    microphoneDeviceId: runtime.microphoneDeviceId ?? null,
    settings,
  });
  try {
    assertMicrophoneResumeCurrent(isCurrent);
    await runtime.mixer.replaceMicrophone(stream);
    assertMicrophoneResumeCurrent(isCurrent);
    runtime.sources.microphoneStream = stream;
    runtime.microphoneReleased = false;
    runtime.captureReport = {
      ...runtime.captureReport,
      ...describeCaptureSettings(settings),
      requested_microphone_device_id: runtime.microphoneDeviceId ?? null,
      requested_microphone_label:
        runtime.captureReport.requested_microphone_device_id === runtime.microphoneDeviceId
          ? runtime.captureReport.requested_microphone_label : null,
      browser_microphone_track: describeTrack(stream.getAudioTracks()[0]),
      notes: ["reopened_microphone_after_pause", ...(usedDefault ? ["microphone_default_fallback"] : [])],
    };
    return usedDefault;
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw error;
  }
};
