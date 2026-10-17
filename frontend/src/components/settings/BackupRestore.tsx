"use client";

import { useEffect, useRef, useState } from "react";
import {
  deleteLatestBackup,
  downloadBackupFile,
  getLatestBackup,
  uploadBackupArchive,
  type LatestBackup,
} from "@/lib/api";
import {
  describeBackupKind,
  formatBackupTime,
  formatFileSize,
} from "@/lib/backupMessages";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import { useRestoreStore } from "@/lib/restoreStore";
import {
  Download,
  Upload,
  Loader2,
  CheckCircle,
  FileArchive,
  Trash2,
  AlertTriangle,
} from "lucide-react";
import { useBackupStore } from "@/lib/backupStore";
import ConfirmationModal from "@/components/ConfirmationModal";
import RestoreOptionsModal from "@/components/settings/RestoreOptionsModal";
import BackupOptionsModal from "@/components/settings/BackupOptionsModal";
import { Badge } from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";

/** Turns the machine-readable reason codes into something an operator can act on. */
const SKIP_REASON_LABELS: Record<string, string> = {
  unresolved_owner:
    "the user or parent record they belong to was not part of this restore",
  no_identity: "the backup did not identify them well enough to match",
  insert_failed: "the database rejected them",
};

const TABLE_LABELS: Record<string, string> = {
  users: "Users",
  recordings: "Meetings",
  transcripts: "Transcripts",
  recording_speakers: "Meeting speakers",
  global_speakers: "People",
  documents: "Attached documents",
  tags: "Tags",
  p_tags: "People tags",
  user_tasks: "Tasks",
  chat_messages: "Chat messages",
  calendar_connections: "Calendar connections",
  calendar_sources: "Calendars",
  calendar_events: "Calendar events",
};

export default function BackupRestore() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isValidZip, setIsValidZip] = useState<boolean>(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [showRestoreOptions, setShowRestoreOptions] = useState(false);
  const [showBackupOptions, setShowBackupOptions] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [latest, setLatest] = useState<LatestBackup | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const { addNotification } = useNotificationStore();

  // Driven by the global BackupPoller, so the panel keeps reporting even if the user
  // navigates away and comes back mid-export.
  const exportTaskId = useBackupStore((state) => state.taskId);
  const exportProgress = useBackupStore((state) => state.progress);
  const readyExportId = useBackupStore((state) => state.readyExport?.export_id);
  const setTaskId = useBackupStore((state) => state.setTaskId);
  const setReadyExport = useBackupStore((state) => state.setReadyExport);

  // Driven by the global RestorePoller once the archive is uploaded.
  const uploading = useRestoreStore((state) => state.uploading);
  const uploadProgress = useRestoreStore((state) => state.uploadPercent);
  const restoreJobId = useRestoreStore((state) => state.jobId);
  const processingStatus = useRestoreStore((state) => state.status);
  const outcome = useRestoreStore((state) => state.outcome);
  const beginUpload = useRestoreStore((state) => state.beginUpload);
  const setUploadProgress = useRestoreStore((state) => state.setUploadProgress);
  const setRestoreStatus = useRestoreStore((state) => state.setStatus);
  const startRestoreJob = useRestoreStore((state) => state.startJob);
  const finishRestore = useRestoreStore((state) => state.finish);
  const dismissOutcome = useRestoreStore((state) => state.dismissOutcome);
  const importing = uploading || restoreJobId !== null;

  const exportPercent =
    exportProgress?.total && exportProgress.total > 0
      ? Math.min(
          100,
          Math.round(((exportProgress.current ?? 0) / exportProgress.total) * 100),
        )
      : null;

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  // Reloaded when an export starts or finishes here, and after a delete.
  useEffect(() => {
    let cancelled = false;
    getLatestBackup()
      .then((value) => {
        if (cancelled) return;
        setLatest(value);
        // A backup started from another device or by another administrator is
        // followed here too, so this browser shows its progress and its notification.
        if (value.job && !useBackupStore.getState().taskId) {
          setTaskId(value.job.task_id);
        }
      })
      .catch((error: unknown) => {
        console.error("Failed to load the latest backup:", error);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey, exportTaskId, readyExportId, setTaskId]);

  const latestExport = latest?.export ?? null;

  const handleDownloadLatest = () => {
    if (!latestExport) return;
    downloadBackupFile(latestExport.export_id);
    if (readyExportId === latestExport.export_id) {
      setReadyExport(null);
    }
  };

  const handleDeleteLatest = async () => {
    try {
      await deleteLatestBackup();
      if (latestExport && readyExportId === latestExport.export_id) {
        setReadyExport(null);
      }
      addNotification({ type: "success", message: "Saved backup deleted." });
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Failed to delete the saved backup."),
      });
    } finally {
      setRefreshKey((key) => key + 1);
    }
  };

  const validateFile = (file: File): boolean => {
    const nameValid = file.name.toLowerCase().endsWith(".zip");
    const typeValid =
      !file.type ||
      file.type === "application/zip" ||
      file.type === "application/x-zip-compressed";

    return nameValid && typeValid;
  };

  const handleFileSelect = (file: File) => {
    if (validateFile(file)) {
      setSelectedFile(file);
      setIsValidZip(true);
      setFileError(null);
    } else {
      setSelectedFile(null);
      setIsValidZip(false);
      setFileError("Please select a valid .zip backup file.");
    }
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget === dropZoneRef.current) {
      setIsDragging(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      handleFileSelect(files[0]);
    }
  };

  const handleRemoveFile = () => {
    setSelectedFile(null);
    setIsValidZip(false);
    setFileError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleRestoreClick = () => {
    if (!selectedFile) return;
    setShowRestoreOptions(true);
  };

  const performRestore = async (clear: boolean, overwrite: boolean) => {
    if (!selectedFile) return;

    setShowRestoreOptions(false);
    beginUpload();
    setRestoreStatus("Preparing upload...");

    try {
      // Writes its progress to the store, so it keeps reporting if the panel unmounts.
      const jobId = await uploadBackupArchive(
        selectedFile,
        clear,
        overwrite,
        setUploadProgress,
        setRestoreStatus,
      );
      startRestoreJob(jobId);
      setSelectedFile(null);
      setIsValidZip(false);
    } catch (error: unknown) {
      console.error("Import failed:", error);
      finishRestore({
        kind: "failed",
        message: getErrorMessage(error, "Failed to restore backup."),
        skipped: null,
      });
    }
  };

  const dismissAndReload = () => {
    dismissOutcome();
    window.location.reload();
  };

  const outcomeIsSuccess = outcome?.kind === "completed";

  return (
    <SettingsCard
      id="backup-export"
      title="Backup and Restore"
      description="Export application data as a restorable archive, and recover it transactionally."
    >
      <SettingsBlock contentClassName="space-y-6">
        {/* Export Section */}
        <div className="pb-6 border-b border-surface-border">
          <h4 className="text-sm font-medium text-foreground mb-2">
            Export Backup
          </h4>
          <p className="text-sm contrast-helper mb-4">
            Download a zip file containing your database, recordings, and
            settings, tasks from the Task List, people voiceprints, and calendar
            data.
            <br />
            <span className="text-xs text-action-text font-medium">
              AI and Hugging Face keys stay redacted. Calendar provider
              credentials and connected calendar tokens are included so calendar
              integrations restore correctly. Treat backup files as sensitive.
            </span>
          </p>

          {latestExport && (
            <div className="mb-4 rounded-lg border border-surface-border bg-surface-inset p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-foreground">
                      Latest backup
                    </span>
                    {latest?.current === true && (
                      <Badge tone="success" size="sm">
                        Up to date
                      </Badge>
                    )}
                    {latest?.current === false && (
                      <Badge tone="warning" size="sm">
                        Changes since
                      </Badge>
                    )}
                  </div>
                  <p className="text-xs contrast-helper">
                    {formatBackupTime(latestExport.created_at)} ·{" "}
                    {describeBackupKind(latestExport)} ·{" "}
                    {formatFileSize(latestExport.size_bytes)}
                  </p>
                  <p className="text-xs contrast-helper">
                    Kept on the server until a newer backup replaces it.
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    onClick={handleDownloadLatest}
                    iconLeft={<Download aria-hidden="true" className="h-4 w-4" />}
                  >
                    Download
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setConfirmDelete(true)}
                    iconLeft={<Trash2 aria-hidden="true" className="h-4 w-4" />}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            </div>
          )}

          {exportTaskId ? (
            <div className="rounded-lg border border-action-border bg-action-tint p-4">
              <div className="flex items-center gap-3 mb-2">
                <Loader2 className="w-4 h-4 animate-spin text-action-text shrink-0" />
                <span className="text-sm font-medium text-foreground">
                  {exportProgress?.status ?? "Preparing backup..."}
                </span>
              </div>

              {/* A determinate bar while the server can count what it is doing, and an
                  indeterminate shimmer otherwise. Either way the panel shows work is
                  happening. */}
              {exportPercent !== null ? (
                <div className="h-2 w-full rounded-full bg-action-tint overflow-hidden">
                  <div
                    className="h-full bg-action transition-all duration-500"
                    style={{ width: `${exportPercent}%` }}
                  />
                </div>
              ) : (
                <div className="h-2 w-full rounded-full bg-action-tint overflow-hidden">
                  <div className="h-full w-1/3 bg-action animate-pulse" />
                </div>
              )}

              <p className="mt-2 text-xs contrast-helper">
                Compressing a large library can take hours. You can leave this
                page or close the tab, and a notification with a download button
                appears when the backup is ready.
              </p>
            </div>
          ) : (
            <button
              onClick={() => setShowBackupOptions(true)}
              className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-card text-action-on bg-action hover:bg-action-hover focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-action disabled:opacity-50 transition-colors"
            >
              <Download className="w-4 h-4 mr-2" />
              Download Backup
            </button>
          )}
        </div>

        {/* Import Section */}
        <div>
          <h4 className="text-sm font-medium text-foreground mb-2">
            Import Backup
          </h4>
          <p className="text-sm contrast-helper mb-4">
            Restore data from a previously exported backup file.
          </p>

          <div className="space-y-4">
            {/* Drag & Drop Zone */}
            <div
              ref={dropZoneRef}
              onClick={() => !importing && fileInputRef.current?.click()}
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
              className={`
                  relative border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors
                  ${
                    isDragging
                      ? "border-action bg-action-tint"
                      : selectedFile
                        ? "border-status-success-border bg-status-success-bg"
                        : "border-control-border hover:border-action-border"
                  }
                  ${importing ? "pointer-events-none opacity-75" : ""}
                `}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleFileSelect(f);
                }}
                className="hidden"
              />

              {selectedFile ? (
                <div className="space-y-2">
                  <FileArchive className="w-12 h-12 mx-auto text-status-success-fg" />
                  <p className="font-medium text-foreground truncate max-w-xs mx-auto">
                    {selectedFile.name}
                  </p>
                  <p className="text-sm text-contrast-helper">
                    {formatFileSize(selectedFile.size)}
                  </p>
                  {!importing && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRemoveFile();
                      }}
                      className="text-sm text-status-danger-fg hover:text-status-danger-fg underline flex items-center justify-center gap-1 mx-auto"
                    >
                      <Trash2 className="w-3 h-3" /> Remove
                    </button>
                  )}
                </div>
              ) : (
                <div className="space-y-2">
                  <Upload className="w-12 h-12 mx-auto text-contrast-helper" />
                  <p className="text-contrast-muted">
                    <span className="font-medium text-action-text">
                      Click to browse
                    </span>{" "}
                    or drag and drop
                  </p>
                  <p className="text-xs contrast-helper">ZIP files only</p>
                </div>
              )}
            </div>

            {/* Upload Progress */}
            {importing && (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-contrast-muted">
                    {uploading
                      ? "Uploading... (keep this tab open)"
                      : "Restoring on the server..."}
                  </span>
                  <span className="text-foreground font-medium">
                    {uploadProgress}%
                  </span>
                </div>
                <div className="w-full bg-surface-inset rounded-full h-2">
                  <div
                    className={`h-2 rounded-full transition-all duration-300 ${uploadProgress === 100 ? "bg-status-success-bg animate-pulse" : "bg-action"}`}
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
                {processingStatus && (
                  <p className="mt-1 text-center text-xs italic contrast-helper">
                    {processingStatus}
                  </p>
                )}
              </div>
            )}

            <button
              onClick={handleRestoreClick}
              disabled={!isValidZip || importing}
              className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-card text-action-on bg-action hover:bg-action-hover focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-action disabled:opacity-50 transition-colors"
            >
              {importing ? (
                <>
                  <Loader2 className="animate-spin -ml-1 mr-2 h-4 w-4" />
                  Restoring...
                </>
              ) : (
                <>
                  <Upload className="-ml-1 mr-2 h-4 w-4" />
                  Restore Backup
                </>
              )}
            </button>
          </div>
        </div>

        {fileError && (
          <div className="p-4 rounded-md bg-status-danger-bg text-status-danger-fg">
            <div className="flex">
              <AlertTriangle className="h-5 w-5 mr-2" />
              <p className="text-sm font-medium">{fileError}</p>
            </div>
          </div>
        )}

        {outcome && (
          <div
            className={`p-4 rounded-md ${outcomeIsSuccess ? "bg-status-success-bg text-status-success-fg" : "bg-status-danger-bg text-status-danger-fg"}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex">
                {outcomeIsSuccess ? (
                  <CheckCircle className="h-5 w-5 mr-2 shrink-0" />
                ) : (
                  <AlertTriangle className="h-5 w-5 mr-2 shrink-0" />
                )}
                <p className="text-sm font-medium">{outcome.message}</p>
              </div>
              {(outcome.kind === "failed" || outcome.kind === "unknown") && (
                <button
                  onClick={dismissOutcome}
                  className="text-xs font-medium underline shrink-0"
                >
                  Dismiss
                </button>
              )}
              {outcomeIsSuccess && (
                <button
                  onClick={dismissAndReload}
                  className="text-xs font-medium underline shrink-0"
                >
                  Reload
                </button>
              )}
            </div>
          </div>
        )}

        {outcome?.kind === "warnings" && outcome.skipped && (
          <div className="rounded-md border border-status-warning-border bg-status-warning-bg p-4">
            <div className="flex items-start justify-between gap-3 mb-3">
              <h5 className="text-sm font-semibold text-status-warning-fg">
                Records That Could Not Be Restored
              </h5>
              <button
                onClick={dismissAndReload}
                className="text-xs font-medium text-status-warning-fg underline shrink-0"
              >
                Dismiss and refresh
              </button>
            </div>
            <ul className="space-y-2">
              {Object.entries(outcome.skipped).map(([table, reasons]) => (
                <li key={table} className="text-xs text-status-warning-fg">
                  <span className="font-medium">
                    {TABLE_LABELS[table] ?? table}
                  </span>
                  <ul className="mt-1 ml-4 list-disc space-y-0.5">
                    {Object.entries(reasons).map(([reason, count]) => (
                      <li key={reason}>
                        {count} skipped because{" "}
                        {SKIP_REASON_LABELS[reason] ?? reason}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </div>
        )}
      </SettingsBlock>
      <RestoreOptionsModal
        isOpen={showRestoreOptions}
        onClose={() => setShowRestoreOptions(false)}
        onConfirm={performRestore}
        fileName={selectedFile?.name || "backup.zip"}
      />
      <BackupOptionsModal
        isOpen={showBackupOptions}
        onClose={() => setShowBackupOptions(false)}
        onRequested={() => setRefreshKey((key) => key + 1)}
      />
      <ConfirmationModal
        isOpen={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={handleDeleteLatest}
        title="Delete Saved Backup"
        message="This deletes the backup kept on the server to free its disk space. Copies you have already downloaded are not affected."
        confirmText="Delete"
        isDangerous={true}
      />
    </SettingsCard>
  );
}
