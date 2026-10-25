"use client";

import { ButtonBase, InputBase } from "@/components/ui/nativeBases";
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
  FileArchive,
  Trash2,
} from "lucide-react";
import { useBackupStore } from "@/lib/backupStore";
import ConfirmationModal from "@/components/ConfirmationModal";
import RestoreOptionsModal from "@/components/settings/RestoreOptionsModal";
import BackupOptionsModal from "@/components/settings/BackupOptionsModal";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import {
  SETTINGS_BUTTON_DANGER,
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_BUTTON_SECONDARY,
} from "./settingsControls";

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

  const openFilePicker = () => {
    if (!importing) fileInputRef.current?.click();
  };

  return (
    <>
      <SettingsCard
        id="backup-export"
        title="Back up"
        description="A zip of the database, recordings, settings, tasks, voiceprints and calendar data."
      >
        <SettingsBlock>
          <SettingsCallout tone="warning" title="Keep backups somewhere safe">
            They hold the calendar app credentials and each person&apos;s
            connected calendar tokens, so calendar sign-in survives a restore. AI
            and Hugging Face keys are left out.
          </SettingsCallout>
        </SettingsBlock>

        {latestExport && (
          <SettingsRow
            label="Latest backup"
            badge={
              latest?.current === true ? (
                <Badge tone="success" size="sm">
                  Up to date
                </Badge>
              ) : latest?.current === false ? (
                <Badge tone="warning" size="sm">
                  Changes since
                </Badge>
              ) : undefined
            }
            description={
              <>
                <span className="block">
                  {formatBackupTime(latestExport.created_at)},{" "}
                  {describeBackupKind(latestExport)},{" "}
                  {formatFileSize(latestExport.size_bytes)}.
                </span>
                <span className="block">
                  Kept on the server until a newer backup replaces it.
                </span>
              </>
            }
            controlClassName="@min-[26rem]:min-w-0"
          >
            <div className="flex flex-wrap gap-2 @min-[26rem]:justify-end">
              <ButtonBase
                chrome="action"
                type="button"
                onClick={handleDownloadLatest}
                className={SETTINGS_BUTTON_SECONDARY}
              >
                <Download aria-hidden="true" className="h-4 w-4" />
                Download
              </ButtonBase>
              <ButtonBase
                chrome="action"
                type="button"
                onClick={() => setConfirmDelete(true)}
                className={SETTINGS_BUTTON_DANGER}
              >
                <Trash2 aria-hidden="true" className="h-4 w-4" />
                Delete
              </ButtonBase>
            </div>
          </SettingsRow>
        )}

        {exportTaskId ? (
          <SettingsBlock
            label={exportProgress?.status ?? "Preparing backup..."}
          >
            {/* A determinate bar while the server can count what it is doing,
                and an indeterminate shimmer otherwise. */}
            <div
              role="progressbar"
              aria-label="Backup progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={exportPercent ?? undefined}
              className="h-2 w-full overflow-hidden rounded-full bg-action-tint"
            >
              {exportPercent !== null ? (
                <div
                  className="h-full bg-action transition-all duration-500"
                  style={{ width: `${exportPercent}%` }}
                />
              ) : (
                <div className="h-full w-1/3 animate-pulse bg-action" />
              )}
            </div>
            <p className="mt-2 text-xs contrast-helper">
              Compressing a large library can take hours. You can leave this
              page or close the tab; a notification with a download button
              appears when the backup is ready.
            </p>
          </SettingsBlock>
        ) : (
          <SettingsRow
            label="New backup"
            description="Choose what goes in it and how far to compress it."
          >
            <div className="flex @min-[26rem]:justify-end">
              <ButtonBase
                chrome="action"
                type="button"
                onClick={() => setShowBackupOptions(true)}
                className={SETTINGS_BUTTON_PRIMARY}
              >
                <Download className="h-4 w-4" aria-hidden="true" />
                Back up now
              </ButtonBase>
            </div>
          </SettingsRow>
        )}
      </SettingsCard>

      <SettingsCard
        id="backup-restore"
        title="Restore"
        description="Bring data back from a backup zip. You choose whether it merges with or replaces what is here before anything changes."
      >
        <SettingsBlock>
          <div
            ref={dropZoneRef}
            role="button"
            tabIndex={importing ? -1 : 0}
            aria-label={selectedFile ? `Selected ${selectedFile.name}. Choose another backup file` : "Choose a backup file"}
            onClick={openFilePicker}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                openFilePicker();
              }
            }}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            className={cn(
              "relative cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
              isDragging
                ? "border-action bg-action-tint"
                : selectedFile
                  ? "border-status-success-border bg-status-success-bg"
                  : "border-control-border hover:border-action-border",
              importing && "pointer-events-none opacity-75",
            )}
          >
            <InputBase
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
                <FileArchive className="mx-auto h-10 w-10 text-status-success-fg" aria-hidden="true" />
                <p className="mx-auto max-w-xs truncate font-medium text-foreground">
                  {selectedFile.name}
                </p>
                <p className="text-sm contrast-helper">
                  {formatFileSize(selectedFile.size)}
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <Upload className="mx-auto h-10 w-10 contrast-icon-muted" aria-hidden="true" />
                <p className="text-sm text-foreground">
                  <span className="font-medium text-action-text">Choose a file</span>{" "}
                  or drop it here
                </p>
                <p className="text-xs contrast-helper">A .zip backup from Nojoin</p>
              </div>
            )}
          </div>

          {selectedFile && !importing && (
            <ButtonBase
              chrome="action"
              type="button"
              onClick={handleRemoveFile}
              className={cn(SETTINGS_BUTTON_SECONDARY, "mt-3")}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Remove file
            </ButtonBase>
          )}

          {importing && (
            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="contrast-helper">
                  {uploading
                    ? "Uploading. Keep this tab open."
                    : "Restoring on the server..."}
                </span>
                <span className="font-medium tabular-nums text-foreground">
                  {uploadProgress}%
                </span>
              </div>
              <div
                role="progressbar"
                aria-label="Restore progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={uploadProgress}
                className="h-2 w-full overflow-hidden rounded-full bg-action-tint"
              >
                <div
                  className={cn(
                    "h-2 rounded-full transition-all duration-300 bg-action",
                    uploadProgress === 100 && "animate-pulse",
                  )}
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
              {processingStatus && (
                <p className="text-xs contrast-helper">{processingStatus}</p>
              )}
            </div>
          )}

          <div className="mt-4 flex justify-end">
            <ButtonBase
              chrome="action"
              type="button"
              onClick={handleRestoreClick}
              disabled={!isValidZip || importing}
              className={SETTINGS_BUTTON_PRIMARY}
            >
              {importing ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Upload className="h-4 w-4" aria-hidden="true" />
              )}
              {importing ? "Restoring..." : "Restore"}
            </ButtonBase>
          </div>
        </SettingsBlock>

        {fileError && (
          <SettingsBlock>
            <SettingsCallout tone="error" message={fileError} />
          </SettingsBlock>
        )}

        {outcome && (
          <SettingsBlock>
            <SettingsCallout tone={outcomeIsSuccess ? "success" : "error"}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <p className="font-medium">{outcome.message}</p>
                {(outcome.kind === "failed" || outcome.kind === "unknown") && (
                  <ButtonBase
                    type="button"
                    onClick={dismissOutcome}
                    className="shrink-0 text-xs font-semibold underline"
                  >
                    Dismiss
                  </ButtonBase>
                )}
                {outcomeIsSuccess && (
                  <ButtonBase
                    type="button"
                    onClick={dismissAndReload}
                    className="shrink-0 text-xs font-semibold underline"
                  >
                    Reload
                  </ButtonBase>
                )}
              </div>
            </SettingsCallout>
          </SettingsBlock>
        )}

        {outcome?.kind === "warnings" && outcome.skipped && (
          <SettingsBlock>
            <SettingsCallout tone="warning" title="Some records could not be restored">
              <ul className="mt-1 space-y-2 text-xs">
                {Object.entries(outcome.skipped).map(([table, reasons]) => (
                  <li key={table}>
                    <span className="font-medium">
                      {TABLE_LABELS[table] ?? table}
                    </span>
                    <ul className="ml-4 mt-1 list-disc space-y-0.5">
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
              <ButtonBase
                type="button"
                onClick={dismissAndReload}
                className="mt-3 text-xs font-semibold underline"
              >
                Dismiss and reload
              </ButtonBase>
            </SettingsCallout>
          </SettingsBlock>
        )}

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
          title="Delete the saved backup"
          message="This deletes the backup kept on the server to free its disk space. Copies you have already downloaded are not affected."
          confirmText="Delete"
          isDangerous={true}
        />
      </SettingsCard>
    </>
  );
}
