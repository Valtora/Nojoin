"use client";

import { useState, useRef } from "react";
import { uploadBackupChunked, type RestoreSkipSummary } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
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
import RestoreOptionsModal from "@/components/settings/RestoreOptionsModal";
import BackupOptionsModal from "@/components/settings/BackupOptionsModal";
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

const countSkippedRows = (skipped?: RestoreSkipSummary | null): number =>
  Object.values(skipped ?? {}).reduce(
    (total, reasons) =>
      total + Object.values(reasons).reduce((sum, count) => sum + count, 0),
    0,
  );

const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
};

export default function BackupRestore() {
  const [importing, setImporting] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isValidZip, setIsValidZip] = useState<boolean>(false);
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [processingStatus, setProcessingStatus] = useState<string>("");
  const [showRestoreOptions, setShowRestoreOptions] = useState(false);
  const [showBackupOptions, setShowBackupOptions] = useState(false);
  const [skipReport, setSkipReport] = useState<RestoreSkipSummary | null>(null);

  // Driven by the global BackupPoller, so the panel keeps reporting even if the user
  // navigates away and comes back mid-export.
  const exportTaskId = useBackupStore((state) => state.taskId);
  const exportProgress = useBackupStore((state) => state.progress);

  const exportPercent =
    exportProgress?.total && exportProgress.total > 0
      ? Math.min(
          100,
          Math.round(((exportProgress.current ?? 0) / exportProgress.total) * 100),
        )
      : null;

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  const handleExportClick = () => {
    setShowBackupOptions(true);
  };

  const validateFile = (file: File): boolean => {
    const nameValid = file.name.toLowerCase().endsWith(".zip");
    const typeValid =
      !file.type ||
      file.type === "application/zip" ||
      file.type === "application/x-zip-compressed";

    if (nameValid && typeValid) {
      return true;
    }
    return false;
  };

  const handleFileSelect = (file: File) => {
    if (validateFile(file)) {
      setSelectedFile(file);
      setIsValidZip(true);
      setMessage(null);
    } else {
      setSelectedFile(null);
      setIsValidZip(false);
      setMessage({
        type: "error",
        text: "Please select a valid .zip backup file.",
      });
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
    setMessage(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleRestoreClick = () => {
    if (!selectedFile) return;
    setShowRestoreOptions(true); // Open the options modal
  };

  const performRestore = async (clear: boolean, overwrite: boolean) => {
    if (!selectedFile) return;

    try {
      setImporting(true);
      setUploadProgress(0);
      setMessage(null);
      setSkipReport(null);
      setShowRestoreOptions(false); // Close the modal

      setProcessingStatus("Preparing upload...");

      // Use chunked upload for robustness
      const result = await uploadBackupChunked(
        selectedFile,
        clear,
        overwrite,
        (progress) => setUploadProgress(progress),
        (status) => setProcessingStatus(status),
      );

      setSelectedFile(null);
      setIsValidZip(false);

      const skipped = result?.skipped;
      const skippedRows = countSkippedRows(skipped);

      if (skippedRows > 0) {
        // The restore succeeded but did not bring everything across. Reporting that as a
        // plain success, then reloading the page two seconds later, would hide it
        // entirely, so the report stays on screen until the operator dismisses it.
        setSkipReport(skipped ?? null);
        setMessage({
          type: "error",
          text: `Backup restored, but ${skippedRows} record${skippedRows === 1 ? "" : "s"} could not be restored. Review the details below, then refresh the page.`,
        });
        return;
      }

      setMessage({
        type: "success",
        text: "Backup restored successfully. Please refresh the page.",
      });

      // Refresh page after short delay to show success message
      setTimeout(() => {
        window.location.reload();
      }, 2000);
    } catch (error: unknown) {
      console.error("Import failed:", error);
      const errorMsg = getErrorMessage(error, "Failed to restore backup.");
      setMessage({ type: "error", text: errorMsg });
    } finally {
      setImporting(false);
      setUploadProgress(0);
      setProcessingStatus("");
    }
  };

  return (
    <SettingsCard
      id="backup-export"
      title="Backup and restore"
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
          {exportTaskId ? (
            <div className="rounded-lg border border-action-border bg-action-tint p-4">
              <div className="flex items-center gap-3 mb-2">
                <Loader2 className="w-4 h-4 animate-spin text-action-text shrink-0" />
                <span className="text-sm font-medium text-foreground">
                  {exportProgress?.status ?? "Preparing backup..."}
                </span>
              </div>

              {/* A determinate bar while the server can count what it is doing, and an
                  indeterminate shimmer otherwise. Either way the panel proves work is
                  happening, which is what the silent version could not do. */}
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
                Compressing a large library can take several minutes. The download
                starts automatically when it is ready, and you can leave this page
                without interrupting it.
              </p>
            </div>
          ) : (
            <button
              onClick={handleExportClick}
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
                    {uploadProgress < 100
                      ? "Uploading..."
                      : "Processing on server (Do not close)..."}
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

        {message && (
          <div
            className={`p-4 rounded-md ${message.type === "success" ? "bg-status-success-bg text-status-success-fg" : "bg-status-danger-bg text-status-danger-fg"}`}
          >
            <div className="flex">
              {message.type === "success" ? (
                <CheckCircle className="h-5 w-5 mr-2" />
              ) : (
                <AlertTriangle className="h-5 w-5 mr-2" />
              )}
              <p className="text-sm font-medium">{message.text}</p>
            </div>
          </div>
        )}

        {skipReport && (
          <div className="rounded-md border border-status-warning-border bg-status-warning-bg p-4">
            <div className="flex items-start justify-between gap-3 mb-3">
              <h5 className="text-sm font-semibold text-status-warning-fg">
                Records that could not be restored
              </h5>
              <button
                onClick={() => {
                  setSkipReport(null);
                  window.location.reload();
                }}
                className="text-xs font-medium text-status-warning-fg underline shrink-0"
              >
                Dismiss and refresh
              </button>
            </div>
            <ul className="space-y-2">
              {Object.entries(skipReport).map(([table, reasons]) => (
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
      />
    </SettingsCard>
  );
}
