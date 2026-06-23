import type {
  RecordingId,
  TranscriptSegment,
  TranscriptSpeakerAssignment,
  TranscriptUtteranceList,
} from "@/types";
import api from "./client";

export const updateTranscriptSegmentSpeaker = async (
  recordingId: RecordingId,
  segmentIndex: number,
  assignment: TranscriptSpeakerAssignment,
): Promise<void> => {
  await api.put(`/transcripts/${recordingId}/segments/${segmentIndex}`, {
    new_speaker_name: assignment.name,
    global_speaker_id: assignment.globalSpeakerId,
    diarization_label: assignment.diarizationLabel,
  });
};

export const updateTranscriptUtteranceSpeaker = async (
  recordingId: RecordingId,
  utteranceId: string,
  assignment: TranscriptSpeakerAssignment,
): Promise<void> => {
  await api.patch(`/transcripts/${recordingId}/utterances/${utteranceId}/speaker`, {
    new_speaker_name: assignment.name,
    global_speaker_id: assignment.globalSpeakerId,
    diarization_label: assignment.diarizationLabel,
    scope: assignment.scope,
  });
};

export const getTranscriptUtterances = async (
  recordingId: RecordingId,
  afterRevision?: number,
): Promise<TranscriptUtteranceList> => {
  const params = new URLSearchParams();

  if (afterRevision !== undefined) {
    params.set("after_revision", String(afterRevision));
  }

  const suffix = params.toString();
  const response = await api.get<TranscriptUtteranceList>(
    `/transcripts/${recordingId}/utterances${suffix ? `?${suffix}` : ""}`,
  );
  return response.data;
};

export const updateTranscriptSegmentText = async (
  recordingId: RecordingId,
  segmentIndex: number,
  text: string,
): Promise<void> => {
  await api.put(`/transcripts/${recordingId}/segments/${segmentIndex}/text`, {
    text,
  });
};

export const updateTranscriptUtteranceText = async (
  recordingId: RecordingId,
  utteranceId: string,
  text: string,
  expectedRevision?: number,
): Promise<void> => {
  await api.patch(`/transcripts/${recordingId}/utterances/${utteranceId}/text`, {
    text,
    expected_revision: expectedRevision,
  });
};

export const findAndReplace = async (
  recordingId: RecordingId,
  find: string,
  replace: string,
  options: { caseSensitive?: boolean; useRegex?: boolean } = {},
): Promise<void> => {
  await api.post(`/transcripts/${recordingId}/replace`, {
    find_text: find,
    replace_text: replace,
    case_sensitive: options.caseSensitive ?? false,
    use_regex: options.useRegex ?? false,
  });
};

export const updateTranscriptSegments = async (
  recordingId: RecordingId,
  segments: TranscriptSegment[],
): Promise<void> => {
  await api.put(`/transcripts/${recordingId}/segments`, { segments });
};
