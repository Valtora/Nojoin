import type { CaptureMode } from "./shared";
import type { CaptureSettings } from "./shared";

export interface CaptureDeviceReport {
  device_id: string;
  label: string;
}

export interface CaptureTrackReport {
  kind: string;
  label: string | null;
  enabled: boolean;
  muted: boolean;
  ready_state: string;
  settings: Record<string, string | number | boolean | null>;
}

export interface CaptureSourceReportSnapshot {
  mode: CaptureMode;
  requested_microphone_device_id: string | null;
  requested_microphone_label: string | null;
  available_microphones: CaptureDeviceReport[];
  browser_microphone_track: CaptureTrackReport | null;
  browser_display_audio_track: CaptureTrackReport | null;
  browser_display_video_track: CaptureTrackReport | null;
  shared_audio_available: boolean;
  configured_microphone_gain: number;
  configured_system_gain: number;
  configured_echo_cancellation: boolean;
  configured_noise_suppression: boolean;
  configured_auto_gain_control: boolean;
  notes: string[];
}

export interface CaptureSourceReportPayload
  extends CaptureSourceReportSnapshot {
  attempt_kind: "start" | "resume";
  outcome: "success" | "failure";
  failure_code: string | null;
  failure_message: string | null;
}

const TRACK_SETTING_KEYS = [
  "deviceId",
  "groupId",
  "displaySurface",
  "channelCount",
  "sampleRate",
  "sampleSize",
  "echoCancellation",
  "noiseSuppression",
  "autoGainControl",
  "latency",
] as const;

const coerceSettingValue = (value: unknown) => {
  if (value == null) {
    return null;
  }
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  return String(value);
};

export const sanitizeTrackSettings = (
  settings: MediaTrackSettings | undefined,
) => {
  const sanitized: Record<string, string | number | boolean | null> = {};
  if (!settings) {
    return sanitized;
  }

  for (const key of TRACK_SETTING_KEYS) {
    const value = coerceSettingValue(
      settings[key as keyof MediaTrackSettings],
    );
    if (value !== null) {
      sanitized[key] = value;
    }
  }

  return sanitized;
};

export const describeTrack = (
  track: MediaStreamTrack | null | undefined,
): CaptureTrackReport | null => {
  if (!track) {
    return null;
  }

  const settings =
    typeof track.getSettings === "function" ? track.getSettings() : undefined;

  return {
    kind: track.kind,
    label: track.label || null,
    enabled: Boolean(track.enabled),
    muted: Boolean(track.muted),
    ready_state: track.readyState,
    settings: sanitizeTrackSettings(settings),
  };
};

export const listAvailableMicrophones = async (
  mediaDevices?: MediaDevices,
): Promise<CaptureDeviceReport[]> => {
  const resolvedMediaDevices = mediaDevices ?? navigator.mediaDevices;
  if (!resolvedMediaDevices?.enumerateDevices) {
    return [];
  }

  const devices = await resolvedMediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === "audioinput")
    .map((device, index) => ({
      device_id: device.deviceId,
      label: device.label || `Microphone ${index + 1}`,
    }));
};

export const buildCaptureSourceReportPayload = (
  snapshot: CaptureSourceReportSnapshot,
  options: {
    attempt_kind: "start" | "resume";
    outcome: "success" | "failure";
    failure_code?: string | null;
    failure_message?: string | null;
    notes?: string[];
  },
): CaptureSourceReportPayload => ({
  ...snapshot,
  notes: [...snapshot.notes, ...(options.notes ?? [])],
  attempt_kind: options.attempt_kind,
  outcome: options.outcome,
  failure_code: options.failure_code ?? null,
  failure_message: options.failure_message ?? null,
});

export const logCaptureSourceReport = (
  report: CaptureSourceReportPayload,
) => {
  const log =
    report.outcome === "failure" ? console.warn : console.info;
  log("[capture] source report", report);
};

export const describeCaptureSettings = (
  settings: CaptureSettings,
): Pick<
  CaptureSourceReportSnapshot,
  | "configured_microphone_gain"
  | "configured_system_gain"
  | "configured_echo_cancellation"
  | "configured_noise_suppression"
  | "configured_auto_gain_control"
> => ({
  configured_microphone_gain: settings.microphoneGain,
  configured_system_gain: settings.systemGain,
  configured_echo_cancellation: settings.echoCancellation,
  configured_noise_suppression: settings.noiseSuppression,
  configured_auto_gain_control: settings.autoGainControl,
});
