import type { CaptureMode } from "./shared";

export type PickSourceErrorCode =
  | "display_denied"
  | "microphone_denied"
  | "missing_display_audio"
  | "unsupported";

export class PickSourceError extends Error {
  code: PickSourceErrorCode;

  constructor(code: PickSourceErrorCode, message: string) {
    super(message);
    this.code = code;
    this.name = "PickSourceError";
  }
}

export interface PickSourceOptions {
  mode?: CaptureMode;
  microphoneDeviceId?: string | null;
  mediaDevices?: MediaDevices;
}

export interface PickedCaptureSources {
  mode: CaptureMode;
  displayStream: MediaStream | null;
  microphoneStream: MediaStream;
  release: () => void;
}

const stopTracks = (stream: MediaStream | null | undefined) => {
  if (!stream) {
    return;
  }

  stream.getTracks().forEach((track) => track.stop());
};

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
  options: PickSourceOptions = {},
): Promise<PickedCaptureSources> => {
  const mode = options.mode ?? "shared_audio";
  const mediaDevices = readMediaDevices(mode, options.mediaDevices);
  let displayStream: MediaStream | null = null;
  let microphoneStream: MediaStream | null = null;

  if (mode === "shared_audio") {
    try {
      displayStream = await mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
        systemAudio: "include",
        selfBrowserSurface: "include",
        surfaceSwitching: "include",
      } as DisplayMediaStreamOptions & Record<string, unknown>);
    } catch (error) {
      throw new PickSourceError(
        "display_denied",
        error instanceof Error
          ? error.message
          : "Display capture was cancelled before the recording started.",
      );
    }

    if (displayStream.getAudioTracks().length === 0) {
      stopTracks(displayStream);
      throw new PickSourceError(
        "missing_display_audio",
        "Please tick Share audio in the picker and try again.",
      );
    }
  }

  try {
    microphoneStream = await mediaDevices.getUserMedia({
      audio: {
        deviceId: options.microphoneDeviceId
          ? { exact: options.microphoneDeviceId }
          : undefined,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    });
  } catch (error) {
    stopTracks(displayStream);
    throw new PickSourceError(
      "microphone_denied",
      error instanceof Error
        ? error.message
        : "Microphone capture was cancelled before the recording started.",
    );
  }

  return {
    mode,
    displayStream,
    microphoneStream,
    release: () => {
      stopTracks(displayStream);
      stopTracks(microphoneStream);
    },
  };
};
