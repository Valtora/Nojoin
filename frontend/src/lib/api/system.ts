import type {
  AdminHealthStatus,
  AsyncTaskStatus,
  RecordingId,
  TelemetryStatus,
} from "@/types";
import api from "./client";

export const getTelemetryStatus = async (): Promise<TelemetryStatus> => {
  const response = await api.get<TelemetryStatus>("/system/telemetry");
  return response.data;
};

export const updateTelemetryEnabled = async (
  enabled: boolean,
): Promise<TelemetryStatus> => {
  const response = await api.post<TelemetryStatus>("/system/telemetry", {
    enabled,
  });
  return response.data;
};

/**
 * Records that the telemetry notice reached an admin's screen, which is what
 * starts the grace period. Called on first render of the banner rather than
 * inferred from a session existing, so the clock only starts when the notice
 * could actually have been read.
 */
export const markTelemetryNoticeShown = async (): Promise<TelemetryStatus> => {
  const response = await api.post<TelemetryStatus>(
    "/system/telemetry/notice-shown",
  );
  return response.data;
};

export const getTlsFingerprint = async (): Promise<{ fingerprint: string | null }> => {
  try {
    const response = await api.get<{ fingerprint: string | null }>("/system/fingerprint");
    return response.data;

    } catch (error: unknown) {
    console.error("Failed to fetch TLS fingerprint", error);
    return { fingerprint: null };
  }
};

export const getDemoRecording = async (): Promise<{ id: RecordingId | null }> => {
  const response = await api.get<{ id: RecordingId | null }>(
    "/system/demo-recording",
  );
  return response.data;
};

export const getTaskStatus = async (taskId: string): Promise<AsyncTaskStatus> => {
  const response = await api.get<AsyncTaskStatus>(`/system/tasks/${taskId}`);
  return response.data;
};

export const getAdminHealth = async (): Promise<AdminHealthStatus> => {
  const response = await api.get<AdminHealthStatus>("/system/admin-health");
  return response.data;
};

export const seedDemoData = async (): Promise<void> => {
  await api.post("/system/seed-demo");
};
