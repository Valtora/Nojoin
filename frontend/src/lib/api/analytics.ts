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
