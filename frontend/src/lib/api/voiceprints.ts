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
