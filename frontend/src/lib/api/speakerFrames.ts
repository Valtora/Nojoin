import { AxiosError } from "axios";

import type { RecordingId } from "@/types";
import api from "./client";

/**
 * The screen study's endpoints (#653). Every one answers 404 unless the
 * installation has `speaker_frame_sampling` set to `owner` and this is the
 * primary Owner.
 */

export interface SpeakerFrameAvailability {
  rate_hz: number;
  retention_days: number;
  max_frame_bytes: number;
}

export interface SpeakerFrameSummary {
  frames: number;
  bytes: number;
  /** ISO 8601, UTC. */
  delete_after: string;
  recording_stopped: boolean;
}

export interface SpeakerFrameUploadResult extends SpeakerFrameSummary {
  status: "stored" | "duplicate";
}

const isNotFound = (error: unknown) =>
  error instanceof AxiosError && error.response?.status === 404;

/** The study's settings for this account, or null when it may not use it. */
export const getSpeakerFrameAvailability =
  async (): Promise<SpeakerFrameAvailability | null> => {
    try {
      const response = await api.get<SpeakerFrameAvailability>(
        "/recordings/speaker-frames/availability",
      );
      return response.data;
    } catch (error: unknown) {
      if (isNotFound(error)) {
        return null;
      }
      throw error;
    }
  };

export const uploadSpeakerFrame = async (
  recordingId: RecordingId,
  frame: { sequence: number; offsetMs: number; blob: Blob },
): Promise<SpeakerFrameUploadResult> => {
  const formData = new FormData();
  formData.append("file", frame.blob, `${frame.sequence}-${frame.offsetMs}.jpg`);
  const response = await api.post<SpeakerFrameUploadResult>(
    `/recordings/${recordingId}/speaker-frames`,
    formData,
    {
      params: { sequence: frame.sequence, offset_ms: frame.offsetMs },
      headers: { "Content-Type": "multipart/form-data" },
    },
  );
  return response.data;
};

/** This recording's frames on the server, or null when the study is off for it. */
export const getSpeakerFrameStatus = async (
  recordingId: RecordingId,
): Promise<SpeakerFrameSummary | null> => {
  try {
    const response = await api.get<SpeakerFrameSummary>(
      `/recordings/${recordingId}/speaker-frames`,
    );
    return response.data;
  } catch (error: unknown) {
    if (isNotFound(error)) {
      return null;
    }
    throw error;
  }
};

export const deleteSpeakerFrames = async (
  recordingId: RecordingId,
): Promise<{ deleted: number }> => {
  const response = await api.delete<{ deleted: number }>(
    `/recordings/${recordingId}/speaker-frames`,
  );
  return response.data;
};
