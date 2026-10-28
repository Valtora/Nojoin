import type {
  GlobalSpeaker,
  GlobalSpeakerUpdate,
  LiveSpeakerNames,
  MergedRecordingSpeaker,
  RecalibrateResult,
  Recording,
  RecordingId,
  RecordingSpeaker,
  SegmentSelection,
  SpeakerSegment,
  SpeakerSplitResult,
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
  data: GlobalSpeakerUpdate,
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
): Promise<RecordingSpeaker[]> => {
  const response = await api.put<RecordingSpeaker[]>(
    `/speakers/recordings/${recordingId}`,
    {
      diarization_label: diarizationLabel,
      global_speaker_name: newName,
    },
  );
  return response.data;
};

/** Confirms a speaker's suggested name; answers like a rename. */
export const confirmSpeakerName = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<RecordingSpeaker[]> => {
  const response = await api.post<RecordingSpeaker[]>(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/suggestions/accept`,
  );
  return response.data;
};

/** Drops a speaker's suggested name, so they are "Speaker N" again. */
export const dismissSpeakerName = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<RecordingSpeaker[]> => {
  const response = await api.post<RecordingSpeaker[]>(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/suggestions/reject`,
  );
  return response.data;
};

/**
 * Names live speaker `label` ("Speaker 3") while the meeting is recorded or
 * paused. A person's exact name links them; the name reaches the processed
 * speaker who holds that speech. Answers with every live name.
 */
export const nameLiveSpeaker = async (
  recordingId: RecordingId,
  label: string,
  name: string,
  createPerson = false,
): Promise<Record<string, string>> => {
  const response = await api.put<LiveSpeakerNames>(
    `/speakers/recordings/${recordingId}/live/${encodeURIComponent(label)}`,
    { name, ...(createPerson ? { create_person: true } : {}) },
  );
  return response.data.live_speaker_names;
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

export const getMergedRecordingSpeakers = async (
  recordingId: RecordingId,
): Promise<MergedRecordingSpeaker[]> => {
  const response = await api.get<MergedRecordingSpeaker[]>(
    `/speakers/recordings/${recordingId}/merged-speakers`,
  );
  return response.data;
};

export const unmergeRecordingSpeaker = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<Recording> => {
  const response = await api.post<Recording>(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/unmerge`,
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
): Promise<RecalibrateResult> => {
  const response = await api.post<RecalibrateResult>(
    `/speakers/${speakerId}/recalibrate`,
    segments,
  );
  return response.data;
};

export const splitSpeaker = async (
  speakerId: number,
  newSpeakerName: string,
  segments: SegmentSelection[],
): Promise<SpeakerSplitResult> => {
  const response = await api.post<SpeakerSplitResult>(
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
