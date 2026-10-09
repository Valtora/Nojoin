import type {
  CalendarEventLink,
  Recording,
  RecordingId,
  RecordingInfo,
  RecordingInitResponse,
  ReprocessRequest,
} from "@/types";
import { isAxiosError } from "@/lib/errors";

import api, { API_BASE_URL } from "./client";

export interface RecordingFilters {
  q?: string;
  start_date?: string;
  end_date?: string;
  speaker_ids?: number[];
  tag_ids?: number[];
  include_archived?: boolean;
  include_deleted?: boolean;
  only_archived?: boolean;
  only_deleted?: boolean;
}

export const getRecordings = async (
  filters?: RecordingFilters,
): Promise<Recording[]> => {
  const params = new URLSearchParams();

  if (filters) {
    if (filters.q) params.append("q", filters.q);
    if (filters.start_date) params.append("start_date", filters.start_date);
    if (filters.end_date) params.append("end_date", filters.end_date);
    if (filters.speaker_ids) {
      filters.speaker_ids.forEach((id) =>
        params.append("speaker_ids", id.toString()),
      );
    }
    if (filters.tag_ids) {
      filters.tag_ids.forEach((id) => params.append("tag_ids", id.toString()));
    }
    if (filters.include_archived) params.append("include_archived", "true");
    if (filters.include_deleted) params.append("include_deleted", "true");
    if (filters.only_archived) params.append("only_archived", "true");
    if (filters.only_deleted) params.append("only_deleted", "true");
  }

  const response = await api.get<Recording[]>(
    `/recordings/?${params.toString()}`,
  );
  return response.data;
};

export const getRecording = async (id: RecordingId): Promise<Recording> => {
  const response = await api.get<Recording>(`/recordings/${id}`);
  return response.data;
};

export const initRecording = async (name?: string): Promise<RecordingInitResponse> => {
  const params = new URLSearchParams();

  if (name?.trim()) {
    params.set("name", name.trim());
  }

  const suffix = params.toString();
  const response = await api.post<RecordingInitResponse>(
    `/recordings/init${suffix ? `?${suffix}` : ""}`,
  );
  return response.data;
};

export const linkRecordingCalendarEvent = async (
  recordingId: RecordingId,
  calendarEventId: number | null,
): Promise<Recording> => {
  const response = await api.put<Recording>(
    `/recordings/${recordingId}/calendar-event`,
    { calendar_event_id: calendarEventId },
  );
  return response.data;
};

export const getRecordingCalendarEventCandidates = async (
  recordingId: RecordingId,
): Promise<CalendarEventLink[]> => {
  const response = await api.get<CalendarEventLink[]>(
    `/recordings/${recordingId}/calendar-event/candidates`,
  );
  return response.data;
};

export const deleteRecording = async (id: RecordingId): Promise<void> => {
  await api.delete(`/recordings/${id}`);
};

export const renameRecording = async (
  id: RecordingId,
  name: string,
): Promise<Recording> => {
  const response = await api.patch<Recording>(`/recordings/${id}`, { name });
  return response.data;
};

export const reprocessRecording = async (
  id: RecordingId,
  body: ReprocessRequest,
): Promise<Recording> => {
  const response = await api.post<Recording>(
    `/recordings/${id}/reprocess`,
    body,
  );
  return response.data;
};

export const getRecordingStreamUrl = (id: RecordingId): string => {
  return `${API_BASE_URL}/recordings/${id}/stream`;
};

export const archiveRecording = async (id: RecordingId): Promise<Recording> => {
  const response = await api.post<Recording>(`/recordings/${id}/archive`);
  return response.data;
};

export const restoreRecording = async (id: RecordingId): Promise<Recording> => {
  const response = await api.post<Recording>(`/recordings/${id}/restore`);
  return response.data;
};

export const softDeleteRecording = async (id: RecordingId): Promise<Recording> => {
  const response = await api.post<Recording>(`/recordings/${id}/soft-delete`);
  return response.data;
};

export const permanentlyDeleteRecording = async (id: RecordingId): Promise<void> => {
  await api.delete(`/recordings/${id}/permanent`);
};

export const getRecordingInfo = async (
  recordingId: RecordingId,
): Promise<RecordingInfo> => {
  const response = await api.get<RecordingInfo>(`/recordings/${recordingId}/info`);
  return response.data;
};

export interface ImportAudioOptions {
  name?: string;
  recordedAt?: Date;
  /** Optional upper bound on speakers. Omit for auto-detect (the default). */
  maxSpeakers?: number | null;
  onUploadProgress?: (progress: number) => void;
}

// Finalize extracts the audio of a video file before it answers, which can
// outlast a proxy's timeout (Cloudflare gives up after 100 s) while the server
// carries on. Finalize is idempotent, so a lost answer is retried: the retry
// waits for the running finalize and returns the same recording.
const FINALIZE_ATTEMPTS = 5;
const FINALIZE_RETRY_STATUSES = new Set([502, 503, 504, 524]);

const finalizeChunkedImport = async (
  recordingId: RecordingId,
): Promise<Recording> => {
  for (let attempt = 1; ; attempt += 1) {
    try {
      const response = await api.post<Recording>(
        `/recordings/import/chunked/finalize?recording_id=${recordingId}`,
      );
      return response.data;
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      const answerLost =
        isAxiosError(error) &&
        (status === undefined || FINALIZE_RETRY_STATUSES.has(status));
      if (!answerLost || attempt >= FINALIZE_ATTEMPTS) {
        throw error;
      }
    }
  }
};

export const importAudio = async (
  file: File,
  options?: ImportAudioOptions,
): Promise<Recording> => {
  // Use chunked upload for all files to ensure reliability and bypass Cloudflare limits
  const CHUNK_SIZE = 10 * 1024 * 1024;
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

  // 1. Initialize Import
  const initParams = new URLSearchParams();
  initParams.append("filename", file.name);
  if (options?.name) initParams.append("name", options.name);
  if (options?.recordedAt)
    initParams.append("recorded_at", options.recordedAt.toISOString());
  if (options?.maxSpeakers != null)
    initParams.append("max_speakers", String(options.maxSpeakers));

  const initResponse = await api.post<Recording>(
    `/recordings/import/chunked/init?${initParams.toString()}`,
  );
  const recording = initResponse.data;

  // 2. Upload Chunks
  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, file.size);
    const chunk = file.slice(start, end);

    const formData = new FormData();
    formData.append("file", chunk);

    // Chunked imports keep their existing import-part sequence; browser live capture uses 0-based segments.
    await api.post(
      `/recordings/import/chunked/segment?recording_id=${recording.id}&sequence=${i + 1}`,
      formData,
      {
        headers: { "Content-Type": "multipart/form-data" },
      },
    );

    // Update progress
    if (options?.onUploadProgress) {
      const progress = Math.round(((i + 1) / totalChunks) * 100);
      // Cap at 99% until finalized
      options.onUploadProgress(Math.min(progress, 99));
    }
  }

  // 3. Finalize Import
  const finalized = await finalizeChunkedImport(recording.id);

  if (options?.onUploadProgress) {
    options.onUploadProgress(100);
  }

  return finalized;
};

export const getSupportedAudioFormats = (): string[] => {
  return [
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".webm",
    ".ogg",
    ".flac",
    ".mp4",
    ".wma",
    ".opus",
    // Media containers: the backend reads their audio track only.
    ".mkv",
    ".mka",
    ".mov",
    ".avi",
    ".m4v",
    ".ts",
    ".mts",
    ".mpg",
    ".mpeg",
    ".3gp",
  ];
};

export const batchArchiveRecordings = async (ids: RecordingId[]): Promise<void> => {
  await api.post("/recordings/batch/archive", { recording_ids: ids });
};

export const batchRestoreRecordings = async (ids: RecordingId[]): Promise<void> => {
  await api.post("/recordings/batch/restore", { recording_ids: ids });
};

export const batchSoftDeleteRecordings = async (
  ids: RecordingId[],
): Promise<void> => {
  await api.post("/recordings/batch/soft-delete", { recording_ids: ids });
};

export const batchPermanentlyDeleteRecordings = async (
  ids: RecordingId[],
): Promise<void> => {
  await api.post("/recordings/batch/permanent", { recording_ids: ids });
};
