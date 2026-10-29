/** Microphone-only ownership changes; the capture graph and upload queue survive. */
import type { CaptureMixer } from "./mixer";
import type { PickedCaptureSources } from "./pickSource";
import { reopenMicrophone } from "./reopenMicrophone";
import { CaptureResumeSupersededError, type CaptureSettings } from "./shared";
import { describeCaptureSettings, describeTrack, type CaptureSourceReportSnapshot } from "./sourceReport";

export interface PausedMicrophoneRuntime {
  sources: PickedCaptureSources;
  mixer: CaptureMixer;
  captureReport: CaptureSourceReportSnapshot;
  microphoneReleased?: boolean;
}

/** Call only after the recorder has flushed its pause tail. */
export const releasePausedMicrophone = (runtime: PausedMicrophoneRuntime, enabled: boolean) => {
  if (!enabled || runtime.microphoneReleased) {
    return;
  }
  runtime.mixer.detachMicrophone();
  runtime.sources.microphoneStream.getTracks().forEach((track) => track.stop());
  runtime.microphoneReleased = true;
};

export const assertMicrophoneResumeCurrent = (isCurrent: () => boolean) => {
  if (!isCurrent()) {
    throw new CaptureResumeSupersededError();
  }
};

/** Adopt a fresh input only while the same paused capture still owns this attempt. */
export const reconnectPausedMicrophone = async (
  runtime: PausedMicrophoneRuntime,
  settings: CaptureSettings,
  isCurrent: () => boolean,
): Promise<boolean> => {
  const { stream, usedDefault } = await reopenMicrophone({
    microphoneDeviceId: settings.microphoneDeviceId,
    settings,
  });
  try {
    // A Stop or Discard during the permission prompt must not leave it live.
    assertMicrophoneResumeCurrent(isCurrent);
    runtime.mixer.replaceMicrophone(stream);
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw error;
  }
  runtime.sources.microphoneStream = stream;
  runtime.microphoneReleased = false;
  runtime.captureReport = {
    ...runtime.captureReport,
    ...describeCaptureSettings(settings),
    requested_microphone_device_id: settings.microphoneDeviceId,
    requested_microphone_label:
      runtime.captureReport.requested_microphone_device_id === settings.microphoneDeviceId
        ? runtime.captureReport.requested_microphone_label : null,
    browser_microphone_track: describeTrack(stream.getAudioTracks()[0]),
    notes: ["reopened_microphone_after_pause", ...(usedDefault ? ["microphone_default_fallback"] : [])],
  };
  return usedDefault;
};
