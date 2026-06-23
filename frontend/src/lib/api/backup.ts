import { getErrorStatus } from "@/lib/errors";
import api from "./client";

export const exportBackupAsync = async (
  includeAudio: boolean = true,
): Promise<{ task_id: string }> => {
  const response = await api.post<{ task_id: string }>(
    `/backup/export?include_audio=${includeAudio}`,
  );
  return response.data;
};

export const getBackupStatus = async (
  taskId: string,
): Promise<{ state: string; status: string; result?: unknown }> => {
  const response = await api.get<{
    state: string;
    status: string;
    result?: unknown;
  }>(`/backup/export/${taskId}`);
  return response.data;
};

export const downloadBackupFile = async (taskId: string): Promise<void> => {
  const response = await api.get(`/backup/export/${taskId}/download`, {
    responseType: "blob",
  });

  const blob = new Blob([response.data], { type: "application/zip" });
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;

  // Create filename with timestamp
  const filename = `nojoin_backup_${new Date().toISOString().replace(/[:.]/g, "-")}.zip`;
  a.download = filename;

  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
};

export const importBackup = async (
  file: File,
  clearExisting: boolean,
  overwriteExisting: boolean,
  onProgress?: (progress: number) => void,
  onStatus?: (status: string) => void,
): Promise<void> => {
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

      // Poll for status
      return new Promise<void>((resolve, reject) => {
        const pollInterval = setInterval(async () => {
          try {
            const statusRes = await api.get(`/backup/import/${jobId}`);
            const { status, progress, error } = statusRes.data;

            if (onStatus && progress) {
              onStatus(progress);
            }

            if (status === "completed") {
              clearInterval(pollInterval);
              resolve();
            } else if (status === "failed") {
              clearInterval(pollInterval);
              reject(new Error(error || "Restore failed during processing"));
            }
            // If 'pending' or 'processing', continue polling

                    } catch (err: unknown) {
            // Retries on transient network errors; aborts on 404 (lost job).
            if (getErrorStatus(err) === 404) {
              clearInterval(pollInterval);
              reject(new Error("Restore job lost on server"));
            }
          }
        }, 2000); // Poll every 2 seconds
      });
    }

    // No job_id: the import completed synchronously, so report done.
    if (onProgress) onProgress(100);
    return;

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
): Promise<void> => {
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

    // 4. Poll Status (Reusing logic pattern from importBackup)
    const jobId = completeRes.data.job_id;
    if (jobId) {
      if (onProgress) onProgress(100);
      if (onStatus) onStatus("Processing on server...");

      return new Promise<void>((resolve, reject) => {
        const pollInterval = setInterval(async () => {
          try {
            const statusRes = await api.get(`/backup/import/${jobId}`);
            const { status, progress, error } = statusRes.data;

            if (onStatus && progress) {
              onStatus(progress);
            }

            if (status === "completed") {
              clearInterval(pollInterval);
              resolve();
            } else if (status === "failed") {
              clearInterval(pollInterval);
              reject(new Error(error || "Restore failed during processing"));
            }

                    } catch (err: unknown) {
            console.warn("Polling status failed", err);
            // If 404, job lost?
            if (getErrorStatus(err) === 404) {
              clearInterval(pollInterval);
              reject(new Error("Restore job lost on server"));
            }
          }
        }, 2000);
      });
    }

    } catch (error: unknown) {
    throw error;
  }
};
