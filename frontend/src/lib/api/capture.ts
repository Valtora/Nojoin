import type { CaptureSourceReportPayload } from "@/lib/capture/sourceReport";
import type {
  ActiveRecordingConflictDetail,
  Recording,
  RecordingCaptureLifecycleResponse,
  RecordingId,
} from "@/types";
import api from "./client";

export const pauseRecordingCapture = async (
  recordingId: RecordingId,
): Promise<RecordingCaptureLifecycleResponse> => {
  const response = await api.post<RecordingCaptureLifecycleResponse>(
    `/recordings/${recordingId}/pause`,
  );
  return response.data;
};

export const reportRecordingCaptureSources = async (
  recordingId: RecordingId,
  payload: CaptureSourceReportPayload,
): Promise<void> => {
  await api.post(`/recordings/${recordingId}/capture-source-report`, payload);
};

export const resumeRecordingCapture = async (
  recordingId: RecordingId,
): Promise<RecordingCaptureLifecycleResponse> => {
  const response = await api.post<RecordingCaptureLifecycleResponse>(
    `/recordings/${recordingId}/resume`,
  );
  return response.data;
};

export const finalizeRecordingCapture = async (
  recordingId: RecordingId,
): Promise<Recording> => {
  const response = await api.post<Recording>(`/recordings/${recordingId}/finalize`);
  return response.data;
};

export const discardRecordingCapture = async (
  recordingId: RecordingId,
  discardReason?: string,
): Promise<void> => {
  const params = new URLSearchParams();
  if (discardReason?.trim()) {
    params.set("reason", discardReason.trim());
  }

  const suffix = params.toString();
  await api.post(`/recordings/${recordingId}/discard${suffix ? `?${suffix}` : ""}`);
};

const resolveRecordingSegmentExtension = (contentType: string | undefined) => {
  const normalized = (contentType || "").split(";", 1)[0].trim().toLowerCase();
  switch (normalized) {
    case "audio/wav":
      return "wav";
    case "audio/ogg":
      return "ogg";
    case "audio/mp4":
      return "m4a";
    case "audio/webm":
    default:
      return "webm";
  }
};

export const uploadRecordingSegment = async (
  recordingId: RecordingId,
  sequence: number,
  blob: Blob,
  filename = `${sequence}.${resolveRecordingSegmentExtension(blob.type)}`,
): Promise<{ status: string; segment: number }> => {
  const formData = new FormData();
  formData.append("file", blob, filename);

  const response = await api.post<{ status: string; segment: number }>(
    `/recordings/${recordingId}/segment?sequence=${sequence}`,
    formData,
    {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    },
  );
  return response.data;
};

export const getPausedRecordings = async (): Promise<Recording[]> => {
  const response = await api.get<Recording[]>(
    "/recordings/?status=PAUSED&user=me",
  );
  return response.data;
};

export const isActiveRecordingConflictDetail = (
  detail: unknown,
): detail is ActiveRecordingConflictDetail => {
  if (!detail || typeof detail !== "object") {
    return false;
  }

  const candidate = detail as Partial<ActiveRecordingConflictDetail>;
  return (
    candidate.code === "active_recording_exists" &&
    typeof candidate.message === "string" &&
    typeof candidate.recording_id === "string" &&
    typeof candidate.status === "string"
  );
};
