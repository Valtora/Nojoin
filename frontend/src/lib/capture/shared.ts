import type { Recording, RecordingId } from "@/types";

export type CaptureUnsupportedReason =
  | "firefox"
  | "safari"
  | "macos_chromium"
  | "mobile"
  | "unknown";

export type CaptureMode = "shared_audio" | "microphone_only";

export interface CaptureSupport {
  supported: boolean;
  reason?: CaptureUnsupportedReason;
  mode?: CaptureMode;
}

export type CaptureStatus =
  | "idle"
  | "starting"
  | "recording"
  | "paused"
  | "finalizing"
  | "error";

export interface CaptureLevels {
  system: number;
  microphone: number;
  mixed: number;
}

export interface CaptureSettings {
  microphoneDeviceId: string | null;
}

export interface PausedCaptureContext {
  recordingId: RecordingId;
  lastSequence: number;
  persistedAt: number;
}

export interface CaptureState {
  status: CaptureStatus;
  support: CaptureSupport;
  levels: CaptureLevels;
  error: string | null;
  lastSequence: number;
  elapsedSeconds: number;
  recordingId: RecordingId | null;
  pausedRecording: Recording | null;
  runtimeActive: boolean;
  settings: CaptureSettings;
}

export interface StartCaptureResult {
  recordingId: RecordingId;
  name?: string;
  resumed: boolean;
}

export interface GuardedExitRequest {
  reason: "pagehide" | "beforeunload" | "route-change";
  useBeacon: boolean;
}

export const DEFAULT_CAPTURE_LEVELS: CaptureLevels = {
  system: 0,
  microphone: 0,
  mixed: 0,
};

export const DEFAULT_CAPTURE_SETTINGS: CaptureSettings = {
  microphoneDeviceId: null,
};

const CAPTURE_SETTINGS_STORAGE_KEY = "nojoin.capture.settings";
const PAUSED_CAPTURE_STORAGE_KEY = "nojoin.capture.paused-recording";

export const readCaptureSettings = (): CaptureSettings => {
  if (typeof window === "undefined") {
    return DEFAULT_CAPTURE_SETTINGS;
  }

  try {
    const raw = window.localStorage.getItem(CAPTURE_SETTINGS_STORAGE_KEY);
    if (!raw) {
      return DEFAULT_CAPTURE_SETTINGS;
    }

    const parsed = JSON.parse(raw) as Partial<CaptureSettings>;
    return {
      microphoneDeviceId:
        typeof parsed.microphoneDeviceId === "string"
          ? parsed.microphoneDeviceId
          : null,
    };
  } catch {
    return DEFAULT_CAPTURE_SETTINGS;
  }
};

export const writeCaptureSettings = (settings: CaptureSettings) => {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(
    CAPTURE_SETTINGS_STORAGE_KEY,
    JSON.stringify(settings),
  );
};

export const readPausedCaptureContext = (): PausedCaptureContext | null => {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.sessionStorage.getItem(PAUSED_CAPTURE_STORAGE_KEY);
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw) as Partial<PausedCaptureContext>;
    if (
      typeof parsed.recordingId !== "string" ||
      typeof parsed.lastSequence !== "number" ||
      typeof parsed.persistedAt !== "number"
    ) {
      return null;
    }

    return {
      recordingId: parsed.recordingId,
      lastSequence: parsed.lastSequence,
      persistedAt: parsed.persistedAt,
    };
  } catch {
    return null;
  }
};

export const writePausedCaptureContext = (context: PausedCaptureContext) => {
  if (typeof window === "undefined") {
    return;
  }

  window.sessionStorage.setItem(
    PAUSED_CAPTURE_STORAGE_KEY,
    JSON.stringify(context),
  );
};

export const clearPausedCaptureContext = () => {
  if (typeof window === "undefined") {
    return;
  }

  window.sessionStorage.removeItem(PAUSED_CAPTURE_STORAGE_KEY);
};
