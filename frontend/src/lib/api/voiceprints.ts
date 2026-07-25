import type {
  BatchVoiceprintResponse,
  RecordingId,
  VoiceprintApplyResult,
  VoiceprintExtractResult,
} from "@/types";
import api from "./client";

export const extractVoiceprint = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<VoiceprintExtractResult> => {
  const response = await api.post<VoiceprintExtractResult>(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/voiceprint/extract`,
  );
  return response.data;
};

export type VoiceprintAction =
  | "create_new"
  | "link_existing"
  | "local_only"
  | "force_link";

export const applyVoiceprintAction = async (
  recordingId: RecordingId,
  diarizationLabel: string,
  action: VoiceprintAction,
  options?: { globalSpeakerId?: number; newSpeakerName?: string },
): Promise<VoiceprintApplyResult> => {
  const response = await api.post<VoiceprintApplyResult>(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/voiceprint/apply`,
    {
      action,
      global_speaker_id: options?.globalSpeakerId,
      new_speaker_name: options?.newSpeakerName,
    },
  );
  return response.data;
};

export const deleteVoiceprint = async (
  recordingId: RecordingId,
  diarizationLabel: string,
): Promise<void> => {
  await api.delete(
    `/speakers/recordings/${recordingId}/speakers/${encodeURIComponent(diarizationLabel)}/voiceprint`,
  );
};

export const extractAllVoiceprints = async (
  recordingId: RecordingId,
): Promise<BatchVoiceprintResponse> => {
  const response = await api.post<BatchVoiceprintResponse>(
    `/speakers/recordings/${recordingId}/voiceprints/extract-all`,
  );
  return response.data;
};

export interface VoiceprintMethodStatus {
  current_method_version: number;
  stale_people: number;
  total_people_with_voiceprint: number;
  stale_recording_speakers: number;
  rebuild_required: boolean;
}

/**
 * How many stored voiceprints predate the current extraction method.
 *
 * A voiceprint made by an older method cannot be compared with a new one, so it
 * stops contributing to automatic identification until it is rebuilt.
 */
export const getVoiceprintMethodStatus =
  async (): Promise<VoiceprintMethodStatus> => {
    const response = await api.get<VoiceprintMethodStatus>(
      "/speakers/voiceprints/method-status",
    );
    return response.data;
  };

export const rebuildVoiceprints = async (): Promise<{
  task_id: string;
  status: string;
}> => {
  const response = await api.post<{ task_id: string; status: string }>(
    "/speakers/voiceprints/rebuild",
  );
  return response.data;
};
