import { getErrorStatus } from "@/lib/errors";
import api, { API_BASE_URL } from "./client";

export type ArchiveQuality = "compressed" | "original";

/** table name -> reason -> count of rows the restore could not bring across. */
export type RestoreSkipSummary = Record<string, Record<string, number>>;

export interface RestoreResult {
  /** Present when the restore finished but did not bring everything across. */
  skipped?: RestoreSkipSummary;
}

const POLL_INTERVAL_MS = 2000;

/**
 * Poll a restore job to completion.
 *
 * Resolves for both "completed" and "completed_with_warnings", carrying the skip report
 * so the caller can decide whether the restore is worth reporting as clean.
 */
const pollRestoreJob = (
  jobId: string,
  onStatus?: (status: string) => void,
): Promise<RestoreResult> =>
  new Promise<RestoreResult>((resolve, reject) => {
    const pollInterval = setInterval(async () => {
      try {
        const statusRes = await api.get(`/backup/import/${jobId}`);
        const { status, progress, error, warnings } = statusRes.data;

        if (onStatus && progress) {
          onStatus(progress);
        }

        if (status === "completed" || status === "completed_with_warnings") {
          clearInterval(pollInterval);
          resolve({ skipped: warnings?.skipped });
        } else if (status === "failed") {
          clearInterval(pollInterval);
          reject(new Error(error || "Restore failed during processing"));
        }
        // "pending" and "processing" keep polling.
      } catch (err: unknown) {
        // Retries on transient network errors; aborts on 404 (lost job).
        if (getErrorStatus(err) === 404) {
          clearInterval(pollInterval);
          reject(new Error("Restore job lost on server"));
        }
      }
    }, POLL_INTERVAL_MS);
  });

export const exportBackupAsync = async (
  includeAudio: boolean = true,
  archiveQuality: ArchiveQuality = "compressed",
): Promise<{ task_id: string }> => {
  const response = await api.post<{ task_id: string }>(
    `/backup/export?include_audio=${includeAudio}&archive_quality=${archiveQuality}`,
  );
  return response.data;
};

export interface BackupStatus {
  state: string;
  status: string;
  /** Coarse phase name, e.g. "Compressing audio". */
  stage?: string | null;
  /** Items completed and expected within the current stage, when countable. */
  current?: number | null;
  total?: number | null;
  result?: unknown;
}

export const getBackupStatus = async (
  taskId: string,
): Promise<BackupStatus> => {
  const response = await api.get<BackupStatus>(`/backup/export/${taskId}`);
  return response.data;
};

export const downloadBackupFile = async (taskId: string): Promise<void> => {
  // Navigate to the endpoint rather than fetching it into a Blob. A backup that
  // includes audio can be many gigabytes, and buffering that in a Blob exhausts the
  // tab's memory before a single byte reaches disk. The browser streams it straight to
  // the filesystem instead, with native progress, pause and resume, authenticating with
  // the access_token cookie the API already accepts.
  const a = document.createElement("a");
  a.href = `${API_BASE_URL}/backup/export/${taskId}/download`;
  // The server sets the filename via Content-Disposition; this is only the fallback.
  a.download = "";
  a.rel = "noopener";

  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};

export const importBackup = async (
  file: File,
  clearExisting: boolean,
  overwriteExisting: boolean,
  onProgress?: (progress: number) => void,
  onStatus?: (status: string) => void,
): Promise<RestoreResult> => {
  const formData = new FormData();
  formData.append("file", file);

  try {
    // 1. Upload and Start Job
    const response = await api.post<{ job_id?: string }>(
      `/backup/import?clear_existing=${clearExisting}&overwrite_existing=${overwriteExisting}`,
      formData,
      {
        headers: {
          "Content-Type": "multipart/form-data",
        },
        timeout: 300000, // 5 minutes for upload (large files)
        onUploadProgress: (progressEvent) => {
          if (onProgress && progressEvent.total) {
            const percentCompleted = Math.round(
              (progressEvent.loaded * 100) / progressEvent.total,
            );
            // Cap at 99% during upload, save 100% for completion or processing start
            onProgress(Math.min(percentCompleted, 99));
          }
        },
      },
    );

    // A 202 response carries a job_id to poll; a response without one means the
    // import already completed synchronously.
    const jobId = response.data.job_id;

    if (jobId) {
      if (onProgress) onProgress(100);
      if (onStatus) onStatus("Processing on server...");
      return pollRestoreJob(jobId, onStatus);
    }

    // No job_id: the import completed synchronously, so report done.
    if (onProgress) onProgress(100);
    return {};

    } catch (error: unknown) {
    throw error;
  }
};

interface UploadChunkInitResponse {
  upload_id: string;
}

export const uploadBackupChunked = async (
  file: File,
  clearExisting: boolean,
  overwriteExisting: boolean,
  onProgress: (percent: number) => void,
  onStatus: (status: string) => void,
): Promise<RestoreResult> => {
  // 10MB chunks to stay well under Cloudflare 100MB limit
  const CHUNK_SIZE = 10 * 1024 * 1024;
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

  try {
    // 1. Initialize
    onStatus("Initializing upload...");
    const initRes = await api.post<UploadChunkInitResponse>(
      "/backup/upload/init",
      null,
      {
        params: {
          filename: file.name,
          file_size: file.size,
          total_chunks: totalChunks,
        },
      },
    );
    const { upload_id } = initRes.data;

    // 2. Upload Chunks
    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunk = file.slice(start, end);

      const formData = new FormData();
      // blob name is important
      formData.append("file", chunk, "blob");

      await api.post(`/backup/upload/${upload_id}/chunk`, formData, {
        params: { chunk_index: i },
        headers: { "Content-Type": "multipart/form-data" },
      });

      // Update combined progress
      const percent = Math.round(((i + 1) / totalChunks) * 100);
      onProgress(Math.min(percent, 99)); // Keep 100 for processing
      onStatus(`Uploading part ${i + 1} of ${totalChunks}...`);
    }

    // 3. Complete
    onStatus("Finalizing upload and starting restore...");
    const completeRes = await api.post(
      `/backup/upload/${upload_id}/complete`,
      null,
      {
        params: {
          clear_existing: clearExisting,
          overwrite_existing: overwriteExisting,
        },
      },
    );

    // 4. Poll Status
    const jobId = completeRes.data.job_id;
    if (jobId) {
      if (onProgress) onProgress(100);
      if (onStatus) onStatus("Processing on server...");
      return pollRestoreJob(jobId, onStatus);
    }

    return {};
  } catch (error: unknown) {
    throw error;
  }
};
