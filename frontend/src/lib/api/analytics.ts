import type { RecordingAnalytics, RecordingId } from "@/types";
import api from "./client";

export const getRecordingAnalytics = async (
  recordingId: RecordingId,
): Promise<RecordingAnalytics> => {
  const response = await api.get<RecordingAnalytics>(
    `/transcripts/${recordingId}/analytics`,
  );
  return response.data;
};

export const generateRecordingAnalytics = async (
  recordingId: RecordingId,
): Promise<{ recording_id: string; delivery_status: string }> => {
  const response = await api.post<{
    recording_id: string;
    delivery_status: string;
  }>(`/transcripts/${recordingId}/analytics/generate`);
  return response.data;
};

/** Run the AI analysis pass. Spends the user's AI quota, so it is on request. */
export const generateRecordingAiAnalytics = async (
  recordingId: RecordingId,
): Promise<{ recording_id: string; ai_status: string }> => {
  const response = await api.post<{
    recording_id: string;
    ai_status: string;
  }>(`/transcripts/${recordingId}/analytics/ai/generate`);
  return response.data;
};
