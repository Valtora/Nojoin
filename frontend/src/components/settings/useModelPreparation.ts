"use client";

import { useEffect, useRef, useState } from "react";

import type { DownloadProgress, ModelPreparationTarget } from "@/types";
import { getDownloadProgress, prepareModels } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";

/** Discover first-use downloads too, even if they begin after Settings opens. */
export function useModelPreparation(refreshStatus: () => void) {
  const { addNotification } = useNotificationStore();
  const [downloadProgress, setDownloadProgress] = useState<DownloadProgress | null>(null);
  const [preparing, setPreparing] = useState<ModelPreparationTarget | null>(null);
  const active = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let reading = false;
    const readProgress = async () => {
      if (reading) return;
      reading = true;
      try {
        const progress = await getDownloadProgress();
        if (cancelled) return;
        const wasActive = active.current;
        active.current = progress.in_progress;
        setDownloadProgress(progress);
        // A first-use request can be queued before global progress starts, or
        // finish between polls. Status also discovers pending markers and expiry.
        refreshStatus();
        if (wasActive && !progress.in_progress) {
          setPreparing(null);
          if (progress.status === "complete" || progress.status === "error") {
            addNotification({
              type: progress.status === "complete" ? "success" : "error",
              message: progress.status === "complete"
                ? "Model preparation complete."
                : progress.message || "Model preparation failed.",
            });
          }
        }
      } catch (error: unknown) {
        console.error("Failed to read model preparation progress", error);
      } finally {
        reading = false;
      }
    };
    void readProgress();
    const timer = setInterval(() => void readProgress(), 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [refreshStatus, addNotification]);

  const startPreparation = async (target: ModelPreparationTarget): Promise<boolean> => {
    setPreparing(target);
    try {
      await prepareModels(target);
      active.current = true;
      refreshStatus();
      addNotification({
        type: "success",
        message: "Model preparation started. Progress is shown below.",
      });
      return true;
    } catch (error: unknown) {
      setPreparing(null);
      addNotification({
        type: "error",
        message: `Could not start model preparation: ${getErrorMessage(error, "Unknown error")}`,
      });
      return false;
    }
  };

  return {
    downloadProgress,
    preparing,
    preparationRunning: preparing !== null || (downloadProgress?.in_progress ?? false),
    startPreparation,
  };
}
