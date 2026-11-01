import type { CaptureSourceReportPayload } from "@/lib/capture/sourceReport";
import type {
  ActiveRecordingConflictDetail,
  CaptureSupersededDetail,
  Recording,
  RecordingCaptureLifecycleResponse,
  RecordingId,
} from "@/types";
import api from "./client";

/**
 * The capture epoch a tab sends with its requests for its own capture. Left
 * out, as for a stop or discard from a dialog, the request is not checked.
 */
export interface CaptureEpochOption {
  epoch?: number;
}

export const pauseRecordingCapture = async (
  recordingId: RecordingId,
  options: CaptureEpochOption = {},
): Promise<RecordingCaptureLifecycleResponse> => {
  const response = await api.post<RecordingCaptureLifecycleResponse>(
    `/recordings/${recordingId}/pause`,
    undefined,
    { params: { epoch: options.epoch } },
  );
  return response.data;
};

export const reportRecordingCaptureSources = async (
  recordingId: RecordingId,
  payload: CaptureSourceReportPayload,
): Promise<void> => {
  await api.post(`/recordings/${recordingId}/capture-source-report`, payload);
};

/**
 * Resumes a capture. With the epoch this tab holds, it carries on with its own
 * capture; without one, it takes the capture over and is issued the next epoch.
 * `reclaimEpoch`, the epoch this tab held before its page reloaded, lets that
 * takeover go ahead while the server still holds the capture as uploading.
 * `nextSequence`, the sequence the recorder numbers its new audio from, puts
 * the resume's line break after audio from before the pause that this tab has
 * not uploaded yet (issue #815).
 */
export const resumeRecordingCapture = async (
  recordingId: RecordingId,
  options: CaptureEpochOption & { reclaimEpoch?: number; nextSequence?: number } = {},
): Promise<RecordingCaptureLifecycleResponse> => {
  const response = await api.post<RecordingCaptureLifecycleResponse>(
    `/recordings/${recordingId}/resume`,
    undefined,
    {
      params: {
        epoch: options.epoch,
        reclaim_epoch: options.reclaimEpoch,
        next_sequence: options.nextSequence,
      },
    },
  );
  return response.data;
};

/**
 * Set or clear the speaker cap on an in-progress recording.
 *
 * Diarization does not run until capture stops, so this stays editable for the
 * whole recording and the value present at stop time is the one applied. Pass
 * null for auto-detect, which is the default.
 */
export const updateRecordingMaxSpeakers = async (
  recordingId: RecordingId,
  maxSpeakers: number | null,
): Promise<Recording> => {
  const query =
    maxSpeakers == null ? "" : `?max_speakers=${encodeURIComponent(maxSpeakers)}`;
  const response = await api.put<Recording>(
    `/recordings/${recordingId}/max_speakers${query}`,
  );
  return response.data;
};

export const finalizeRecordingCapture = async (
  recordingId: RecordingId,
  options: { clientElapsedSeconds?: number } & CaptureEpochOption = {},
): Promise<Recording> => {
  // The browser's own recording time, so the server can judge coverage
  // against the capture rather than against the time spent uploading after it.
  const clientElapsedSeconds =
    options.clientElapsedSeconds !== undefined && options.clientElapsedSeconds > 0
      ? options.clientElapsedSeconds
      : undefined;
  const response = await api.post<Recording>(
    `/recordings/${recordingId}/finalize`,
    undefined,
    { params: { client_elapsed_seconds: clientElapsedSeconds, epoch: options.epoch } },
  );
  return response.data;
};

export const discardRecordingCapture = async (
  recordingId: RecordingId,
  discardReason?: string,
  options: CaptureEpochOption = {},
): Promise<void> => {
  const params = new URLSearchParams();
  if (discardReason?.trim()) {
    params.set("reason", discardReason.trim());
  }
  if (options.epoch !== undefined) {
    params.set("epoch", String(options.epoch));
  }

  const suffix = params.toString();
  await api.post(`/recordings/${recordingId}/discard${suffix ? `?${suffix}` : ""}`);
};

const resolveRecordingSegmentExtension = (contentType: string | undefined) => {
  const normalized = (contentType || "").split(";", 1)[0].trim().toLowerCase();
  switch (normalized) {
    case "audio/flac":
      return "flac";
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

/**
 * Uploads one segment. `resumeMark` goes with the first segment of new audio
 * after a resume that restored audio recorded before the pause: the line break
 * that resume stored, which the server moves to this segment (issue #815).
 */
export const uploadRecordingSegment = async (
  recordingId: RecordingId,
  sequence: number,
  blob: Blob,
  options: CaptureEpochOption & { resumeMark?: number } = {},
): Promise<{ status: string; segment: number }> => {
  const formData = new FormData();
  formData.append("file", blob, `${sequence}.${resolveRecordingSegmentExtension(blob.type)}`);

  const response = await api.post<{ status: string; segment: number }>(
    `/recordings/${recordingId}/segment`,
    formData,
    {
      params: { sequence, epoch: options.epoch, resume_mark: options.resumeMark },
      headers: {
        "Content-Type": "multipart/form-data",
      },
    },
  );
  return response.data;
};

/**
 * The user's captures still open: paused ones, and ones still marked as
 * uploading, whether live in some tab or stalled after their tab was lost.
 * Imports in progress come back too, since they share the UPLOADING status.
 */
export const getUnfinishedCaptures = async (): Promise<Recording[]> => {
  const response = await api.get<Recording[]>(
    "/recordings/?status=PAUSED&status=UPLOADING&user=me",
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

export const isCaptureSupersededDetail = (
  detail: unknown,
): detail is CaptureSupersededDetail => {
  if (!detail || typeof detail !== "object") {
    return false;
  }

  const candidate = detail as Partial<CaptureSupersededDetail>;
  return candidate.code === "capture_superseded" && typeof candidate.message === "string";
};

/**
 * Whether the server refused a request because the capture was resumed in
 * another tab or on another device after this tab's epoch was issued.
 */
export const isCaptureSupersededError = (error: unknown): boolean => {
  if (!error || typeof error !== "object" || !("response" in error)) {
    return false;
  }
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    .response;
  return response?.status === 409 && isCaptureSupersededDetail(response.data?.detail);
};

/**
 * What the server refuses every capture request with once the recording has
 * been stopped. It mirrors `UPLOAD_CLOSED_DETAIL` in
 * `backend/api/v1/endpoints/recordings/constants.py`.
 */
const CAPTURE_CLOSED_DETAIL = "Recording is no longer accepting capture uploads";

/**
 * Whether the server refused a request because the recording has been stopped
 * and sent for processing, so it takes no more audio from any tab. A stop from
 * the resume-or-discard dialog issues no epoch, so a tab whose capture was
 * stopped elsewhere hears this rather than `capture_superseded`.
 */
export const isCaptureClosedError = (error: unknown): boolean => {
  if (!error || typeof error !== "object" || !("response" in error)) {
    return false;
  }
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    .response;
  return response?.status === 409 && response.data?.detail === CAPTURE_CLOSED_DETAIL;
};

/**
 * Whether the capture a tab holds has ended for it elsewhere: resumed in
 * another tab or on another device, or stopped. Asking again gets the same
 * answer, so the tab stands down.
 */
export const isCaptureEndedError = (error: unknown): boolean =>
  isCaptureSupersededError(error) || isCaptureClosedError(error);
