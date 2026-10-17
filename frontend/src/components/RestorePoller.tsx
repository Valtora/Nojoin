"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getRestoreStatus } from "@/lib/api";
import { countSkippedRows } from "@/lib/backupMessages";
import { useNotificationStore } from "@/lib/notificationStore";
import { RESTORE_STORE_KEY, useRestoreStore } from "@/lib/restoreStore";

const POLL_INTERVAL_MS = 2000;

/**
 * Follows a restore once its archive is on the server, wherever in the app the person
 * is, and reports how it ended. The settings panel shows the same result, including the
 * records that could not be restored, until it is dismissed there.
 */
export default function RestorePoller() {
  const router = useRouter();
  const jobId = useRestoreStore((state) => state.jobId);
  const setStatus = useRestoreStore((state) => state.setStatus);
  const finish = useRestoreStore((state) => state.finish);
  const addNotification = useNotificationStore((state) => state.addNotification);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const { status, progress, error, warnings } = await getRestoreStatus(jobId);
        if (cancelled) return;

        if (status === "pending" || status === "processing") {
          if (progress) setStatus(progress);
          return;
        }

        const reload = {
          label: "Reload",
          variant: "primary" as const,
          onClick: () => window.location.reload(),
        };

        if (status === "completed") {
          finish({
            kind: "completed",
            message: "Backup restored. Reload the page to see the restored data.",
            skipped: null,
          });
          addNotification({
            type: "success",
            persistent: true,
            message: "Backup restored. Reload Nojoin to see the restored data.",
            actions: [reload],
          });
          return;
        }

        if (status === "completed_with_warnings") {
          const skipped = warnings?.skipped ?? null;
          const count = countSkippedRows(skipped);
          const message = `Backup restored, but ${count} record${count === 1 ? "" : "s"} could not be restored.`;
          finish({
            kind: "warnings",
            message: `${message} Review the details below, then reload the page.`,
            skipped,
          });
          addNotification({
            type: "warning",
            persistent: true,
            message,
            actions: [
              {
                label: "View Details",
                variant: "primary",
                onClick: () => router.push("/settings/backup"),
              },
            ],
          });
          return;
        }

        const message =
          error ||
          (status === "unknown"
            ? "The server is no longer tracking this restore."
            : "Restore failed during processing.");
        finish({ kind: status === "unknown" ? "unknown" : "failed", message, skipped: null });
        addNotification({
          type: status === "unknown" ? "warning" : "error",
          persistent: true,
          message: status === "unknown" ? message : `Restore failed: ${message}`,
        });
      } catch (err: unknown) {
        // Retried on the next tick: a network error says nothing about the restore.
        console.error("Restore polling error:", err);
      }
    };

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [jobId, setStatus, finish, addNotification, router]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === RESTORE_STORE_KEY) {
        void useRestoreStore.persist.rehydrate();
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return null;
}
