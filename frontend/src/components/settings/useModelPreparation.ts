"use client";

import { useEffect, useRef, useState } from "react";

import type { DownloadProgress, ModelPreparationTarget, SystemModelStatus } from "@/types";
import { getDownloadProgress, prepareModels } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";

/** Read once while idle; follow preparations discovered at mount or requested here. */
export function useModelPreparation(
  refreshStatus: () => Promise<SystemModelStatus | null>,
  statusDownloading: boolean,
) {
  const { addNotification } = useNotificationStore();
  const [downloadProgress, setDownloadProgress] = useState<DownloadProgress | null>(null);
  const [preparing, setPreparing] = useState<ModelPreparationTarget | null>(null);
  const active = useRef(false);
  const queueing = useRef(false);
  const reading = useRef(false);
  const mounted = useRef(false);
  const preparationRunning = preparing !== null || statusDownloading || (downloadProgress?.in_progress ?? false);

  useEffect(() => {
    let cancelled = false;
    mounted.current = true;
    reading.current = true;
    void getDownloadProgress().then((progress) => {
      if (!cancelled) setDownloadProgress(progress);
    }).catch((error: unknown) => console.error("Failed to read model preparation progress", error))
      .finally(() => { reading.current = false; });
    return () => { cancelled = true; mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!preparationRunning) return;
    active.current = true;
    let cancelled = false;
    const readProgress = async () => {
      if (reading.current || queueing.current) return;
      reading.current = true;
      try {
        const progress = await getDownloadProgress();
        if (cancelled) return;
        const status = await refreshStatus();
        if (cancelled || queueing.current || status === null) return;
        const running = progress.in_progress || Object.values(status).some((model) => model.state === "downloading");
        const wasActive = active.current;
        active.current = running;
        setDownloadProgress(progress);
        if (!running) {
          setPreparing(null);
          if (wasActive && (progress.status === "complete" || progress.status === "error")) {
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
        reading.current = false;
      }
    };
    const timer = setInterval(() => void readProgress(), 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [refreshStatus, addNotification, preparationRunning]);

  const startPreparation = async (target: ModelPreparationTarget): Promise<boolean> => {
    setPreparing(target);
    queueing.current = true;
    try {
      await prepareModels(target);
      if (!mounted.current) return true;
      active.current = true;
      await refreshStatus();
      if (!mounted.current) return true;
      addNotification({
        type: "success",
        message: "Model preparation started. Progress is shown below.",
      });
      return true;
    } catch (error: unknown) {
      if (!mounted.current) return false;
      setPreparing(null);
      addNotification({
        type: "error",
        message: `Could not start model preparation: ${getErrorMessage(error, "Unknown error")}`,
      });
      return false;
    } finally {
      queueing.current = false;
    }
  };

  return {
    downloadProgress,
    preparing,
    preparationRunning,
    startPreparation,
  };
}
