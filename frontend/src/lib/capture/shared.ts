import type { Recording, RecordingId } from "@/types";

export type CaptureUnsupportedReason =
  | "firefox"
  | "safari"
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
  releaseMicrophoneOnPause: boolean;
  microphoneGain: number;
  systemGain: number;
  echoCancellation: boolean;
  noiseSuppression: boolean;
  autoGainControl: boolean;
}

export interface PausedCaptureContext {
  recordingId: RecordingId;
  lastSequence: number;
  persistedAt: number;
}

export interface FinalizeRetryProgress {
  attempt: number;
  maxAttempts: number;
}

/**
 * Stage the stop sequence has reached. Surfaced so a slow or failed stop names
 * the step it is on instead of looking hung (issue #166).
 */
export type CaptureStopStage =
  | "stopping-recorder"
  | "flushing-uploads"
  | "releasing-media"
  | "finalizing";

/**
 * Why the audio on the server is behind the clock.
 *
 * The shortfall is the same measurement either way, but the two causes need
 * opposite things from the user, so the warning must not guess. A suspended tab
 * means audio is gone for good and they should change something now. An
 * unreachable backend means uploads are queued and will catch up, and telling
 * someone their tab is being suspended when the server was down sends them off
 * to check browser settings that were never the problem.
 *
 * `unknown` is the honest answer when neither signal is present, and gets copy
 * that describes the shortfall without diagnosing it.
 */
export type CaptureCoverageCause =
  | "tab-suspended"
  | "backend-unreachable"
  | "unknown";

/**
 * Divergence between wall-clock recording time and the audio the server holds.
 *
 * `capturedSeconds` is the backend's sum of the segments it decoded, which is
 * what the meeting actually has.
 */
export interface CaptureCoverageWarning {
  capturedSeconds: number;
  elapsedSeconds: number;
  /**
   * Elapsed time the server holds no audio for and the browser is not holding
   * either, so it is genuinely gone. Net of `queuedSeconds`.
   */
  missingSeconds: number;
  /**
   * Recorded, still queued in the browser, not yet acknowledged by the server.
   * This audio is not lost; it uploads when the server answers again.
   */
  queuedSeconds: number;
  cause: CaptureCoverageCause;
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
  /** Lock-free captures needing a decision, independently of an open dialog. */
  attentionRecordings: Recording[];
  runtimeActive: boolean;
  settings: CaptureSettings;
  finalizeRetry: FinalizeRetryProgress | null;
  stopStage: CaptureStopStage | null;
  coverageWarning: CaptureCoverageWarning | null;
  /**
   * Shortfall the user has already dismissed, in seconds, or null if they have
   * not. Kept as a number rather than a flag so a shortfall that keeps growing
   * can raise the warning again instead of staying hidden for the rest of the
   * meeting.
   */
  coverageWarningDismissedAt: number | null;
  /**
   * Audio recorded but not yet acknowledged by the server. Normally a segment
   * or two; it grows when uploads cannot keep up, and it is what a stop waits
   * to send before the recording can be finalized.
   */
  uploadQueue: UploadQueueSize;
}

export interface UploadQueueSize {
  segments: number;
  seconds: number;
}

export interface StartCaptureResult {
  recordingId: RecordingId;
  name?: string;
  resumed: boolean;
}

export type StartCaptureResponse = StartCaptureResult | null;

/**
 * Rejects a Resume that a Stop, Discard or other capture action overtook. That
 * action owns the capture and reports its own outcome, so callers stay quiet.
 */
export class CaptureResumeSupersededError extends Error {
  constructor(options?: ErrorOptions) {
    super("The recording changed while Resume was waiting. Try Resume again if it is still paused.", options);
    this.name = "CaptureResumeSupersededError";
  }
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
  releaseMicrophoneOnPause: false,
  microphoneGain: 1,
  systemGain: 1,
  echoCancellation: true,
  noiseSuppression: false,
  autoGainControl: true,
};

const CAPTURE_SETTINGS_STORAGE_KEY = "nojoin.capture.settings";
const PAUSED_CAPTURE_STORAGE_KEY = "nojoin.capture.paused-recording";
/**
 * Saved with the settings. Settings saved without it predate noise suppression
 * defaulting off (#709). Every save then wrote the old default, on, alongside
 * whatever else changed, so a stored `true` there is not a choice.
 */
const CAPTURE_SETTINGS_VERSION = 2;

export const readCaptureSettings = (): CaptureSettings => {
  if (typeof window === "undefined") {
    return DEFAULT_CAPTURE_SETTINGS;
  }

  try {
    const raw = window.localStorage.getItem(CAPTURE_SETTINGS_STORAGE_KEY);
    if (!raw) {
      return DEFAULT_CAPTURE_SETTINGS;
    }

    const parsed = JSON.parse(raw) as Partial<CaptureSettings> & {
      version?: unknown;
    };
    const settings: CaptureSettings = {
      microphoneDeviceId:
        typeof parsed.microphoneDeviceId === "string"
          ? parsed.microphoneDeviceId
          : null,
      releaseMicrophoneOnPause:
        typeof parsed.releaseMicrophoneOnPause === "boolean"
          ? parsed.releaseMicrophoneOnPause
          : DEFAULT_CAPTURE_SETTINGS.releaseMicrophoneOnPause,
      microphoneGain:
        typeof parsed.microphoneGain === "number" &&
        Number.isFinite(parsed.microphoneGain)
          ? parsed.microphoneGain
          : DEFAULT_CAPTURE_SETTINGS.microphoneGain,
      systemGain:
        typeof parsed.systemGain === "number" &&
        Number.isFinite(parsed.systemGain)
          ? parsed.systemGain
          : DEFAULT_CAPTURE_SETTINGS.systemGain,
      echoCancellation:
        typeof parsed.echoCancellation === "boolean"
          ? parsed.echoCancellation
          : DEFAULT_CAPTURE_SETTINGS.echoCancellation,
      noiseSuppression:
        typeof parsed.noiseSuppression === "boolean"
          ? parsed.noiseSuppression
          : DEFAULT_CAPTURE_SETTINGS.noiseSuppression,
      autoGainControl:
        typeof parsed.autoGainControl === "boolean"
          ? parsed.autoGainControl
          : DEFAULT_CAPTURE_SETTINGS.autoGainControl,
    };

    if (typeof parsed.version !== "number") {
      // Runs once: the settings are saved again with the version, after which
      // a stored `true` is the user's own and is kept.
      settings.noiseSuppression = false;
      try {
        writeCaptureSettings(settings);
      } catch {
        // Storage refused the write; the next read migrates again.
      }
    }

    return settings;
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
    JSON.stringify({ ...settings, version: CAPTURE_SETTINGS_VERSION }),
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
