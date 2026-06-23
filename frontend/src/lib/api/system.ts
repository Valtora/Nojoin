import type { AdminHealthStatus, AsyncTaskStatus, RecordingId } from "@/types";
import api from "./client";

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
