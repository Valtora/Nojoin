"use client";

import { useEffect, useRef } from "react";
import { useBackupStore } from "@/lib/backupStore";
import { getBackupStatus, downloadBackupFile } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";

// A backup that never reaches a terminal state within this window is presumed lost:
// either the worker never received it or its result has expired from the backend. Celery
// cannot tell those apart from "still queued", so the client needs its own stopping rule.
const POLL_TIMEOUT_MS = 6 * 60 * 60 * 1000;

export default function BackupPoller() {
  const { taskId, startedAt, setTaskId } = useBackupStore();
  const { addNotification } = useNotificationStore();
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (!taskId) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    const poll = async () => {
      try {
        const { state, status: statusMsg, result } = await getBackupStatus(taskId);

        if (state !== "SUCCESS" && state !== "FAILURE" && state !== "REVOKED") {
          const elapsed = Date.now() - (startedAt ?? Date.now());
          if (elapsed > POLL_TIMEOUT_MS) {
            setTaskId(null);
            addNotification({
              type: "error",
              message:
                "Gave up waiting for the backup. If it is still running, check the server logs and try again.",
            });
            return;
          }
        }

        if (state === "SUCCESS") {
          // Task complete
          setTaskId(null); // Stop polling immediately

          addNotification({
            type: "success",
            message: "Backup created successfully! Downloading...",
          });

          // Recordings whose audio was missing from disk are archived as metadata only.
          // The archive records this too, but the person who took the backup should be
          // told at the point they take it.
          const warnings = (result as { warnings?: Record<string, number> } | undefined)
            ?.warnings;
          const missingAudio = warnings?.recordings_without_audio ?? 0;
          if (missingAudio > 0) {
            addNotification({
              type: "error",
              message: `${missingAudio} recording${missingAudio === 1 ? "" : "s"} had no audio file on disk and were backed up as metadata only.`,
              persistent: true,
            });
          }

          try {
            await downloadBackupFile(taskId);

                    } catch (err: unknown) {
            console.error(err);
            addNotification({
              type: "error",
              message: "Backup ready but download failed. Please try again.",
            });
          }
        } else if (state === "FAILURE" || state === "REVOKED") {
          setTaskId(null);
          addNotification({
            type: "error",
            message: `Backup creation failed: ${statusMsg}`,
          });
        }
        // PENDING or PROCESSING: Continue polling

            } catch (error: unknown) {
        console.error("Backup polling error:", error);
        // Don't clear taskId immediately on network error, retry.
        // Consideration: Clear polling if 404 is encountered.
      }
    };

    // Poll immediately and then interval
    poll();
    intervalRef.current = setInterval(poll, 2000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [taskId, startedAt, setTaskId, addNotification]);

  return null; // Invisible component
}
