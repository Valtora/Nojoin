"use client";

import { useEffect, useRef } from "react";
import { useNotificationStore } from "@/lib/notificationStore";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import {
  startConnectivityMonitor,
  stopConnectivityMonitor,
  useConnectivityStore,
} from "@/lib/connectivity/monitor";
import { isReachable } from "@/lib/connectivity/reducer";

export default function ServiceStatusAlerts() {
  const { addNotification, removeActiveNotification } = useNotificationStore();
  const status = useConnectivityStore((state) => state.status);
  const { db, worker, deploymentWarnings, refreshHealth, startPolling, stopPolling } =
    useServiceStatusStore();

  const notificationIds = useRef<{ [key: string]: string | null }>({
    reachability: null,
    db: null,
    worker: null,
    placeholderSecrets: null,
  });
  const placeholderWarningSignature = useRef<string>("");

  // Start reachability monitoring and health-content polling for the session.
  useEffect(() => {
    startConnectivityMonitor();
    startPolling();
    return () => {
      stopPolling();
      stopConnectivityMonitor();
    };
  }, [startPolling, stopPolling]);

  // Refresh health *content* when the tab returns to the foreground. Reachability
  // has its own visibility handling inside the connectivity monitor.
  useEffect(() => {
    const refreshOnForeground = () => {
      if (document.visibilityState === "visible") {
        void refreshHealth();
      }
    };

    window.addEventListener("focus", refreshOnForeground);
    document.addEventListener("visibilitychange", refreshOnForeground);

    return () => {
      window.removeEventListener("focus", refreshOnForeground);
      document.removeEventListener("visibilitychange", refreshOnForeground);
    };
  }, [refreshHealth]);

  useEffect(() => {
    const reachable = isReachable(status);

    // Reachability. "Offline" (your device) and "Unreachable" (the backend) are
    // deliberately different messages so users are not misdirected.
    if (!reachable && !notificationIds.current.reachability) {
      notificationIds.current.reachability = addNotification({
        type: "error",
        message:
          status === "offline"
            ? "You're offline. Check your network connection."
            : "Server Unreachable: Cannot connect to Nojoin Backend API.",
        persistent: true,
      });
    } else if (reachable && notificationIds.current.reachability) {
      removeActiveNotification(notificationIds.current.reachability);
      notificationIds.current.reachability = null;
    }

    // DB and worker status are only meaningful while the backend is reachable.
    if (reachable && !db && !notificationIds.current.db) {
      notificationIds.current.db = addNotification({
        type: "error",
        message: "Database Error: Connection to PostgreSQL failed.",
        persistent: true,
      });
    } else if ((!reachable || db) && notificationIds.current.db) {
      removeActiveNotification(notificationIds.current.db);
      notificationIds.current.db = null;
    }

    if (reachable && !worker && !notificationIds.current.worker) {
      notificationIds.current.worker = addNotification({
        type: "error",
        message: "Worker Offline: Background processing is paused.",
        persistent: true,
      });
    } else if ((!reachable || worker) && notificationIds.current.worker) {
      removeActiveNotification(notificationIds.current.worker);
      notificationIds.current.worker = null;
    }

    const nextPlaceholderSignature = deploymentWarnings
      .map((warning) => `${warning.code}:${warning.key}`)
      .sort()
      .join("|");

    if (!reachable || deploymentWarnings.length === 0) {
      if (notificationIds.current.placeholderSecrets) {
        removeActiveNotification(notificationIds.current.placeholderSecrets);
        notificationIds.current.placeholderSecrets = null;
      }
      placeholderWarningSignature.current = "";
      return;
    }

    if (nextPlaceholderSignature === placeholderWarningSignature.current) {
      return;
    }

    if (notificationIds.current.placeholderSecrets) {
      removeActiveNotification(notificationIds.current.placeholderSecrets);
    }

    const affectedKeys = deploymentWarnings
      .map((warning) => warning.key)
      .sort()
      .join(", ");

    notificationIds.current.placeholderSecrets = addNotification({
      type: "warning",
      message: `Security warning: Nojoin is using known placeholder secrets from the deployment templates (${affectedKeys}). Update .env and restart or redeploy Nojoin.`,
      persistent: true,
    });
    placeholderWarningSignature.current = nextPlaceholderSignature;
  }, [
    status,
    db,
    worker,
    deploymentWarnings,
    addNotification,
    removeActiveNotification,
  ]);

  return null;
}
