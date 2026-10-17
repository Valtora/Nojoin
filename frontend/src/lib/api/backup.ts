import api, { API_BASE_URL } from "./client";

export type ArchiveQuality = "compressed" | "original";

/** table name -> reason -> count of rows the restore could not bring across. */
export type RestoreSkipSummary = Record<string, Record<string, number>>;

/** A finished backup archive held on the server, ready to download. */
export interface BackupExport {
  export_id: string;
  /** When its data was read, as an ISO timestamp. */
  created_at: string;
  include_audio: boolean;
  archive_quality: ArchiveQuality;
  size_bytes: number;
  /** Counts of recordings and documents archived without their files. */
  warnings: Record<string, number>;
}

/**
 * The answer to a backup request: either the cached archive, when nothing has changed
 * since it was made, or the id of the build preparing a new one.
 */
export type BackupRequestResult =
  | { status: "ready"; export: BackupExport }
  | { status: "building"; task_id: string };

export const requestBackup = async (
  includeAudio: boolean = true,
  archiveQuality: ArchiveQuality = "compressed",
): Promise<BackupRequestResult> => {
  const response = await api.post<BackupRequestResult>("/backup/export", null, {
    params: { include_audio: includeAudio, archive_quality: archiveQuality },
  });
  return response.data;
};

/**
 * PENDING and PROCESSING while a build is under way. EXPIRED means the archive the
 * build produced has since been replaced or deleted, or the server no longer knows it.
 */
export type BackupState =
  | "PENDING"
  | "PROCESSING"
  | "SUCCESS"
  | "FAILURE"
  | "EXPIRED";

export interface BackupStatus {
  state: BackupState;
  status: string;
  /** Coarse phase name, e.g. "Compressing audio". */
  stage?: string | null;
  /** Items completed and expected within the current stage, when countable. */
  current?: number | null;
  total?: number | null;
  /** The archive, once the state is SUCCESS. */
  export?: BackupExport;
}

export const getBackupStatus = async (taskId: string): Promise<BackupStatus> => {
  const response = await api.get<BackupStatus>(`/backup/export/${taskId}`);
  return response.data;
};

export interface BackupJob {
  task_id: string;
  include_audio: boolean;
  archive_quality: ArchiveQuality;
}

export interface LatestBackup {
  export: BackupExport | null;
  /** Whether the archive still matches the library. Null when unknown. */
  current: boolean | null;
  /** The backup being prepared, if any. */
  job: BackupJob | null;
}

export const getLatestBackup = async (): Promise<LatestBackup> => {
  const response = await api.get<LatestBackup>("/backup/export/latest");
  return response.data;
};

export const deleteLatestBackup = async (): Promise<void> => {
  await api.delete("/backup/export/latest");
};

export const downloadBackupFile = (exportId: string): void => {
  // Navigate to the endpoint rather than fetching it into a Blob. A backup that
  // includes audio can be many gigabytes, and buffering that in a Blob exhausts the
  // tab's memory before a single byte reaches disk. The browser streams it straight to
  // the filesystem instead, with native progress, pause and resume, authenticating with
  // the access_token cookie the API already accepts.
  const a = document.createElement("a");
  a.href = `${API_BASE_URL}/backup/export/${encodeURIComponent(exportId)}/download`;
  // The server sets the filename via Content-Disposition; this is only the fallback.
  a.download = "";
  a.rel = "noopener";

  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};

export type RestoreJobStatus =
  | "pending"
  | "processing"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "unknown";

export interface RestoreStatus {
  status: RestoreJobStatus;
  progress: string;
  error: string | null;
  /** Present when the restore finished but did not bring everything across. */
  warnings: { skipped?: RestoreSkipSummary } | null;
}

export const getRestoreStatus = async (jobId: string): Promise<RestoreStatus> => {
  const response = await api.get<RestoreStatus>(`/backup/import/${jobId}`);
  return response.data;
};

interface UploadChunkInitResponse {
  upload_id: string;
}

/**
 * Upload a backup archive in parts and start restoring it. Resolves with the restore
 * job's id once the server has queued it; the restore itself is followed by polling
 * getRestoreStatus.
 */
export const uploadBackupArchive = async (
  file: File,
  clearExisting: boolean,
  overwriteExisting: boolean,
  onProgress: (percent: number) => void,
  onStatus: (status: string) => void,
): Promise<string> => {
  // 10MB chunks to stay well under Cloudflare 100MB limit
  const CHUNK_SIZE = 10 * 1024 * 1024;
  const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

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

    const percent = Math.round(((i + 1) / totalChunks) * 100);
    onProgress(Math.min(percent, 99)); // Keep 100 for processing
    onStatus(`Uploading part ${i + 1} of ${totalChunks}...`);
  }

  onStatus("Finalizing upload and starting restore...");
  const completeRes = await api.post<{ job_id: string }>(
    `/backup/upload/${upload_id}/complete`,
    null,
    {
      params: {
        clear_existing: clearExisting,
        overwrite_existing: overwriteExisting,
      },
    },
  );

  onProgress(100);
  return completeRes.data.job_id;
};
