"use client";

import { useEffect } from "react";
import { BACKUP_STORE_KEY, useBackupStore } from "@/lib/backupStore";
import { getBackupStatus, downloadBackupFile } from "@/lib/api";
import { archiveWarningNotifications, formatBackupTime } from "@/lib/backupMessages";
import { useNotificationStore } from "@/lib/notificationStore";

const POLL_INTERVAL_MS = 2000;

/**
 * Follows the backup this browser asked for, wherever in the app the person is, and
 * tells them when it is ready. Nothing downloads on its own: a download that starts from
 * a timer hours after the request is easy to miss and some browsers block it, so the
 * notification carries a Download Now button instead. It stays until used or closed,
 * and comes back after a reload.
 */
export default function BackupPoller() {
  const taskId = useBackupStore((state) => state.taskId);
  const readyExport = useBackupStore((state) => state.readyExport);
  const setTaskId = useBackupStore((state) => state.setTaskId);
  const setProgress = useBackupStore((state) => state.setProgress);
  const setReadyExport = useBackupStore((state) => state.setReadyExport);
  const addNotification = useNotificationStore((state) => state.addNotification);
  const dismissToast = useNotificationStore((state) => state.dismissToast);

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const status = await getBackupStatus(taskId);
        if (cancelled) return;

        if (status.state === "PENDING" || status.state === "PROCESSING") {
          // Publish progress so the settings panel can show what the server is doing.
          setProgress({
            status: status.status || "Working...",
            stage: status.stage,
            current: status.current,
            total: status.total,
          });
          return;
        }

        setTaskId(null);
        if (status.state === "SUCCESS" && status.export) {
          archiveWarningNotifications(status.export.warnings).forEach(addNotification);
          setReadyExport(status.export);
        } else if (status.state === "EXPIRED") {
          addNotification({ type: "info", message: status.status });
        } else {
          addNotification({
            type: "error",
            message: `Backup creation failed: ${status.status}`,
          });
        }
      } catch (error: unknown) {
        // The task id is kept so polling retries: a transient network error
        // says nothing about whether the backup is still running.
        console.error("Backup polling error:", error);
      }
    };

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [taskId, setTaskId, setProgress, setReadyExport, addNotification]);

  // Keyed on the id rather than the object, which a rehydrate replaces with an equal copy.
  const readyId = readyExport?.export_id;
  const readyCreatedAt = readyExport?.created_at;

  useEffect(() => {
    if (!readyId || !readyCreatedAt) return;
    const id = `backup-ready-${readyId}`;
    addNotification({
      id,
      type: "success",
      persistent: true,
      message: `Your backup from ${formatBackupTime(readyCreatedAt)} is ready to download.`,
      actions: [
        {
          label: "Download Now",
          variant: "primary",
          onClick: () => {
            downloadBackupFile(readyId);
            setReadyExport(null);
          },
        },
      ],
      onClose: () => setReadyExport(null),
    });
    // Another tab downloading or dismissing it clears it here too, via the storage
    // listener below, and this takes the toast down with it.
    return () => dismissToast(id);
  }, [readyId, readyCreatedAt, addNotification, dismissToast, setReadyExport]);

  useEffect(() => {
    // Zustand's persist writes to localStorage but does not read other tabs' writes.
    const onStorage = (event: StorageEvent) => {
      if (event.key === BACKUP_STORE_KEY) {
        void useBackupStore.persist.rehydrate();
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return null;
}
