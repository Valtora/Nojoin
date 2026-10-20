import type { BackupExport, RestoreSkipSummary } from "@/lib/api";
import type { NewNotification } from "@/lib/notificationStore";
import { getCachedUserTimeZone } from "@/lib/timezone";

export const formatBackupTime = (iso: string): string => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  // In the time zone the rest of the app uses, once the app shell has loaded it.
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: getCachedUserTimeZone() ?? undefined,
  });
};

export const describeBackupKind = (backup: Pick<BackupExport, "include_audio" | "archive_quality">): string => {
  if (!backup.include_audio) return "Without audio";
  return backup.archive_quality === "original" ? "Original audio" : "Compressed audio";
};

export const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
};

/**
 * Recordings archived as metadata only: their audio file was missing from disk, or
 * reading or re-encoding it failed. The archive records this too, but the person taking
 * the backup should be told when they take it.
 */
export const archiveWarningNotifications = (
  warnings: Record<string, number> | undefined,
): NewNotification[] => {
  const notifications: NewNotification[] = [];
  const missingAudio = warnings?.recordings_without_audio ?? 0;
  if (missingAudio > 0) {
    notifications.push({
      type: "error",
      message: `${missingAudio} recording${missingAudio === 1 ? "" : "s"} had no audio file on disk and ${missingAudio === 1 ? "was" : "were"} backed up as metadata only.`,
      persistent: true,
    });
  }
  const failedAudio = warnings?.recordings_audio_failed ?? 0;
  if (failedAudio > 0) {
    notifications.push({
      type: "error",
      message: `The audio of ${failedAudio} recording${failedAudio === 1 ? "" : "s"} could not be archived, so ${failedAudio === 1 ? "it was" : "they were"} backed up as metadata only. Check the worker logs.`,
      persistent: true,
    });
  }
  return notifications;
};

export const countSkippedRows = (skipped?: RestoreSkipSummary | null): number =>
  Object.values(skipped ?? {}).reduce(
    (total, reasons) =>
      total + Object.values(reasons).reduce((sum, count) => sum + count, 0),
    0,
  );
