import type { RecordingId, Transcript } from "@/types";
import api from "./client";

/** Split immediately before a genuine word, against the line shown to the person. */
export const splitTranscriptUtterance = async (
  recordingId: RecordingId,
  utteranceId: string,
  beforeWordId: string,
  expectedRevision: number,
): Promise<Transcript> => {
  const response = await api.post<Transcript>(
    `/transcripts/${recordingId}/utterances/${utteranceId}/split`,
    { before_word_id: beforeWordId, expected_revision: expectedRevision },
  );
  return response.data;
};
