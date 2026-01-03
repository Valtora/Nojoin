"use client";

import { useEffect, useRef } from "react";
import { useBackupStore } from "@/lib/backupStore";
import { getBackupStatus, downloadBackupFile } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";

export default function BackupPoller() {
  const { taskId, setTaskId } = useBackupStore();
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
        const { state, status: statusMsg } = await getBackupStatus(taskId);

        if (state === "SUCCESS") {
          // Task complete
          setTaskId(null); // Stop polling immediately

          addNotification({
            type: "success",
            message: "Backup created successfully! Downloading...",
          });

          try {
            await downloadBackupFile(taskId);
          } catch (err) {
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
      } catch (error) {
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
  }, [taskId, setTaskId, addNotification]);

  return null; // Invisible component
}
