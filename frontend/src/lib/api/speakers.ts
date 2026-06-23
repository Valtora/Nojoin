import type {
  GlobalSpeaker,
  Recording,
  RecordingId,
  RecordingSpeaker,
  SegmentSelection,
  SpeakerSegment,
} from "@/types";
import api from "./client";

export interface GlobalSpeakerFilters {
  q?: string;
  tags?: number[];
}

export const getGlobalSpeakers = async (
  filters?: GlobalSpeakerFilters,
): Promise<GlobalSpeaker[]> => {
  const params = new URLSearchParams();
  if (filters) {
    if (filters.q) params.append("q", filters.q);
    if (filters.tags) {
      filters.tags.forEach((id) => params.append("tags", id.toString()));
    }
  }
  const response = await api.get<GlobalSpeaker[]>(
    `/speakers/?${params.toString()}`,
  );
  return response.data;
};

export const createGlobalSpeaker = async (
  data: Partial<GlobalSpeaker> & { tag_ids?: number[] },
): Promise<GlobalSpeaker> => {
  const response = await api.post<GlobalSpeaker>("/speakers/", data);
  return response.data;
};

export const updateGlobalSpeaker = async (
  id: number,
  data: Partial<GlobalSpeaker> & { tag_ids?: number[] },
): Promise<GlobalSpeaker> => {
  const response = await api.put<GlobalSpeaker>(`/speakers/${id}`, data);
  return response.data;
};

export const mergeSpeakers = async (
  sourceId: number,
  targetId: number,
): Promise<GlobalSpeaker> => {
  const response = await api.post<GlobalSpeaker>("/speakers/merge", {
    source_speaker_id: sourceId,
    target_speaker_id: targetId,
  });
  return response.data;
};

export const deleteGlobalSpeakerEmbedding = async (
  id: number,
): Promise<void> => {
  await api.delete(`/speakers/${id}/embedding`);
};

export const deleteGlobalSpeaker = async (id: number): Promise<void> => {
  await api.delete(`/speakers/${id}`);
};

export const updateSpeaker = async (
  recordingId: RecordingId,
  diarizationLabel: string,
  newName: string,
): Promise<void> => {
  await api.put(`/speakers/recordings/${recordingId}`, {
    diarization_label: diarizationLabel,
    global_speaker_name: newName,
  });
};

export const updateSpeakerColor = async (
  recordingId: RecordingId,
  label: string,
  color: string,
): Promise<void> => {
  await api.put(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(label)}/color`,
    {
      color,
    },
  );
};

export const acceptSpeakerNameSuggestion = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<void> => {
  await api.post(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/suggestions/accept`,
  );
};

export const rejectSpeakerNameSuggestion = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<void> => {
  await api.post(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/suggestions/reject`,
  );
};

export const mergeRecordingSpeakers = async (
  recordingId: RecordingId,
  targetSpeakerLabel: string,
  sourceSpeakerLabel: string,
): Promise<Recording> => {
  const response = await api.post<Recording>(
    `/speakers/recordings/${recordingId}/merge`,
    {
      target_speaker_label: targetSpeakerLabel,
      source_speaker_label: sourceSpeakerLabel,
    },
  );
  return response.data;
};

export const deleteRecordingSpeaker = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<void> => {
  await api.delete(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}`,
  );
};

export const promoteToGlobalSpeaker = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<RecordingSpeaker> => {
  const response = await api.post<RecordingSpeaker>(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/promote`,
  );
  return response.data;
};

export const inferSpeakers = async (recordingId: RecordingId): Promise<void> => {
  await api.post(`/recordings/${recordingId}/infer-speakers`);
};

export const getSpeakerSegments = async (
  speakerId: number,
): Promise<SpeakerSegment[]> => {
  const response = await api.get<SpeakerSegment[]>(
    `/speakers/${speakerId}/segments`,
  );
  return response.data;
};

export const recalibrateSpeaker = async (
  speakerId: number,
  segments: SegmentSelection[],
): Promise<void> => {
  await api.post(`/speakers/${speakerId}/recalibrate`, segments);
};

export const splitSpeaker = async (
  speakerId: number,
  newSpeakerName: string,
  segments: SegmentSelection[],
): Promise<GlobalSpeaker> => {
  const response = await api.post<GlobalSpeaker>(
    `/speakers/${speakerId}/split`,
    {
      new_speaker_name: newSpeakerName,
      segments,
    },
  );
  return response.data;
};

export const splitLocalSpeaker = async (
  recordingId: RecordingId,
  diarizationLabel: string,
  newSpeakerName: string,
  segments: SegmentSelection[],
) => {
  const response = await api.post(
    `/speakers/recordings/${recordingId}/speakers/${diarizationLabel}/split`,
    {
      new_speaker_name: newSpeakerName,
      segments,
    },
  );
  return response.data;
};

export const scanMatches = async (
  speakerId: number,
): Promise<{ matches_found: number; recordings_updated: number }> => {
  const response = await api.post(`/speakers/${speakerId}/scan-matches`);
  return response.data;
};
