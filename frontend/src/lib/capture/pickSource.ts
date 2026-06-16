import {
  DEFAULT_CAPTURE_SETTINGS,
  type CaptureMode,
  type CaptureSettings,
} from "./shared";
import {
  describeCaptureSettings,
  describeTrack,
  listAvailableMicrophones,
  type CaptureSourceReportSnapshot,
} from "./sourceReport";

export type PickSourceErrorCode =
  | "display_cancelled"
  | "display_denied"
  | "microphone_denied"
  | "selected_microphone_unavailable"
  | "microphone_mismatch"
  | "missing_display_audio"
  | "unsupported";

export class PickSourceError extends Error {
  code: PickSourceErrorCode;
  captureReport: CaptureSourceReportSnapshot | null;

  constructor(
    code: PickSourceErrorCode,
    message: string,
    captureReport: CaptureSourceReportSnapshot | null = null,
  ) {
    super(message);
    this.message = message;
    this.code = code;
    this.captureReport = captureReport;
    this.name = "PickSourceError";
    Object.setPrototypeOf(this, PickSourceError.prototype);
  }
}

export interface PickSourceOptions {
  mode?: CaptureMode;
  microphoneDeviceId?: string | null;
  settings: CaptureSettings;
  mediaDevices?: MediaDevices;
}

export interface PickedCaptureSources {
  mode: CaptureMode;
  displayStream: MediaStream | null;
  microphoneStream: MediaStream;
  captureReport: CaptureSourceReportSnapshot;
  release: () => void;
}

const stopTracks = (stream: MediaStream | null | undefined) => {
  if (!stream) {
    return;
  }

  stream.getTracks().forEach((track) => track.stop());
};

const isDisplayCaptureCancellation = (error: unknown) => {
  if (!error || typeof error !== "object") {
    return false;
  }

  const candidate = error as Partial<DOMException> & { message?: unknown };
  const name = typeof candidate.name === "string" ? candidate.name : "";
  const message =
    typeof candidate.message === "string"
      ? candidate.message.toLowerCase()
      : "";

  return (
    name === "AbortError" ||
    name === "NotAllowedError" ||
    message.includes("permission denied by user") ||
    message.includes("cancelled before the recording started")
  );
};

const isUnavailableDeviceError = (error: unknown) => {
  if (!error || typeof error !== "object") {
    return false;
  }

  const candidate = error as Partial<DOMException> & { message?: unknown };
  const name = typeof candidate.name === "string" ? candidate.name : "";
  const message =
    typeof candidate.message === "string"
      ? candidate.message.toLowerCase()
      : "";

  return (
    name === "NotFoundError" ||
    name === "OverconstrainedError" ||
    message.includes("not found") ||
    message.includes("requested device not found") ||
    message.includes("could not start video source")
  );
};

const buildCaptureReportSnapshot = (options: {
  mode: CaptureMode;
  requestedMicrophoneDeviceId: string | null;
  requestedMicrophoneLabel: string | null;
  availableMicrophones: Awaited<ReturnType<typeof listAvailableMicrophones>>;
  displayStream: MediaStream | null;
  microphoneStream: MediaStream | null;
  settings: CaptureSettings;
  notes?: string[];
}): CaptureSourceReportSnapshot => ({
  mode: options.mode,
  requested_microphone_device_id: options.requestedMicrophoneDeviceId,
  requested_microphone_label: options.requestedMicrophoneLabel,
  available_microphones: options.availableMicrophones,
  browser_microphone_track: describeTrack(
    options.microphoneStream?.getAudioTracks()[0],
  ),
  browser_display_audio_track: describeTrack(
    options.displayStream?.getAudioTracks()[0],
  ),
  browser_display_video_track: describeTrack(
    options.displayStream?.getVideoTracks?.()[0],
  ),
  shared_audio_available:
    (options.displayStream?.getAudioTracks().length ?? 0) > 0,
  ...describeCaptureSettings(options.settings),
  notes: [...(options.notes ?? [])],
});

const readMediaDevices = (
  mode: CaptureMode,
  mediaDevices?: MediaDevices,
) => {
  const resolvedMediaDevices = mediaDevices ?? navigator.mediaDevices;
  if (
    !resolvedMediaDevices?.getUserMedia ||
    (mode === "shared_audio" && !resolvedMediaDevices.getDisplayMedia)
  ) {
    throw new PickSourceError(
      "unsupported",
      "This browser does not expose the media capture APIs required for recording.",
    );
  }

  return resolvedMediaDevices;
};

export const pickCaptureSource = async (
  options: PickSourceOptions = { settings: DEFAULT_CAPTURE_SETTINGS },
): Promise<PickedCaptureSources> => {
  const mode = options.mode ?? "shared_audio";
  const mediaDevices = readMediaDevices(mode, options.mediaDevices);
  let displayStream: MediaStream | null = null;
  let microphoneStream: MediaStream | null = null;
  let availableMicrophones = [] as Awaited<
    ReturnType<typeof listAvailableMicrophones>
  >;
  try {
    availableMicrophones = await listAvailableMicrophones(mediaDevices);
  } catch {
    availableMicrophones = [];
  }

  const requestedMicrophoneLabel =
    availableMicrophones.find(
      (device) => device.device_id === options.microphoneDeviceId,
    )?.label ?? null;

  if (
    options.microphoneDeviceId &&
    availableMicrophones.length > 0 &&
    !availableMicrophones.some(
      (device) => device.device_id === options.microphoneDeviceId,
    )
  ) {
    throw new PickSourceError(
      "selected_microphone_unavailable",
      "The selected microphone is not available to the browser. Choose another microphone in Settings > Capture before starting the recording.",
      buildCaptureReportSnapshot({
        mode,
        requestedMicrophoneDeviceId: options.microphoneDeviceId,
        requestedMicrophoneLabel,
        availableMicrophones,
        displayStream: null,
        microphoneStream: null,
        settings: options.settings,
        notes: ["selected_microphone_missing_from_enumerated_devices"],
      }),
    );
  }

  if (mode === "shared_audio") {
    try {
      displayStream = await mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
        systemAudio: "include",
        windowAudio: "system",
        selfBrowserSurface: "include",
        surfaceSwitching: "include",
      } as DisplayMediaStreamOptions & Record<string, unknown>);

    } catch (error: unknown) {
      if (isDisplayCaptureCancellation(error)) {
        throw new PickSourceError(
          "display_cancelled",
          "Display capture was cancelled before the recording started.",
          buildCaptureReportSnapshot({
            mode,
            requestedMicrophoneDeviceId: options.microphoneDeviceId ?? null,
            requestedMicrophoneLabel,
            availableMicrophones,
            displayStream: null,
            microphoneStream: null,
            settings: options.settings,
            notes: ["display_picker_cancelled"],
          }),
        );
      }

      throw new PickSourceError(
        "display_denied",
        error instanceof Error
          ? error.message
          : "Display capture permission was denied before the recording started.",
        buildCaptureReportSnapshot({
          mode,
          requestedMicrophoneDeviceId: options.microphoneDeviceId ?? null,
          requestedMicrophoneLabel,
          availableMicrophones,
          displayStream: null,
          microphoneStream: null,
          settings: options.settings,
          notes: ["display_capture_denied"],
        }),
      );
    }
  }

  try {
    microphoneStream = await mediaDevices.getUserMedia({
      audio: {
        deviceId: options.microphoneDeviceId
          ? { exact: options.microphoneDeviceId }
          : undefined,
        echoCancellation: options.settings.echoCancellation,
        noiseSuppression: options.settings.noiseSuppression,
        autoGainControl: options.settings.autoGainControl,
      },
    });

  } catch (error: unknown) {
    stopTracks(displayStream);
    const selectedDeviceUnavailable =
      Boolean(options.microphoneDeviceId) && isUnavailableDeviceError(error);
    throw new PickSourceError(
      selectedDeviceUnavailable
        ? "selected_microphone_unavailable"
        : "microphone_denied",
      selectedDeviceUnavailable
        ? "The selected microphone is not available to the browser. Choose another microphone in Settings > Capture before starting the recording."
        : error instanceof Error
          ? error.message
          : "Microphone capture was cancelled before the recording started.",
      buildCaptureReportSnapshot({
        mode,
        requestedMicrophoneDeviceId: options.microphoneDeviceId ?? null,
        requestedMicrophoneLabel,
        availableMicrophones,
        displayStream,
        microphoneStream: null,
        settings: options.settings,
        notes: [
          selectedDeviceUnavailable
            ? "selected_microphone_rejected_by_getUserMedia"
            : "microphone_capture_denied",
        ],
      }),
    );
  }

  const microphoneTrack = microphoneStream.getAudioTracks()[0] ?? null;
  const microphoneSettings =
    typeof microphoneTrack?.getSettings === "function"
      ? microphoneTrack.getSettings()
      : undefined;
  const actualMicrophoneDeviceId =
    typeof microphoneSettings?.deviceId === "string"
      ? microphoneSettings.deviceId
      : null;
  if (
    options.microphoneDeviceId &&
    actualMicrophoneDeviceId &&
    actualMicrophoneDeviceId !== options.microphoneDeviceId
  ) {
    const captureReport = buildCaptureReportSnapshot({
      mode,
      requestedMicrophoneDeviceId: options.microphoneDeviceId,
      requestedMicrophoneLabel,
      availableMicrophones,
      displayStream,
      microphoneStream,
      settings: options.settings,
      notes: ["microphone_device_id_mismatch_after_grant"],
    });
    stopTracks(displayStream);
    stopTracks(microphoneStream);
    throw new PickSourceError(
      "microphone_mismatch",
      "The browser did not grant the selected microphone. Recording was stopped to avoid using the wrong input.",
      captureReport,
    );
  }

  const captureReport = buildCaptureReportSnapshot({
    mode,
    requestedMicrophoneDeviceId: options.microphoneDeviceId ?? null,
    requestedMicrophoneLabel,
    availableMicrophones,
    displayStream,
    microphoneStream,
    settings: options.settings,
  });

  return {
    mode,
    displayStream,
    microphoneStream,
    captureReport,
    release: () => {
      stopTracks(displayStream);
      stopTracks(microphoneStream);
    },
  };
};
