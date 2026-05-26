"use client";

import { useEffect, useRef } from "react";
import { useNotificationStore } from "@/lib/notificationStore";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";

export default function ServiceStatusAlerts() {
  const { addNotification, removeActiveNotification } = useNotificationStore();
  const {
    backend,
    db,
    worker,
    backendFailCount,
    checkBackend,
    startPolling,
    stopPolling,
  } = useServiceStatusStore();

  // Track active notification IDs
  const notificationIds = useRef<{ [key: string]: string | null }>({
    backend: null,
    db: null,
    worker: null,
  });

  // Track startup grace period
  const isStartupRef = useRef(true);

  useEffect(() => {
    const timer = setTimeout(() => {
      isStartupRef.current = false;
    }, 5000); // 5 seconds grace period
    return () => clearTimeout(timer);
  }, []);

  // Start polling on mount
  useEffect(() => {
    startPolling();
    return () => stopPolling();
  }, [startPolling, stopPolling]);

  useEffect(() => {
    const refreshStatuses = () => {
      if (document.visibilityState !== "visible") {
        return;
      }

      void checkBackend();
    };

    window.addEventListener("focus", refreshStatuses);
    document.addEventListener("visibilitychange", refreshStatuses);

    return () => {
      window.removeEventListener("focus", refreshStatuses);
      document.removeEventListener("visibilitychange", refreshStatuses);
    };
  }, [checkBackend]);

  // Monitor Service Status
  useEffect(() => {
    const updateNotifications = () => {
      // Backend
      if (!backend && !notificationIds.current.backend) {
        // Shows error only after startup grace period and > 2 failures.
        if (!isStartupRef.current && backendFailCount > 2) {
          notificationIds.current.backend = addNotification({
            type: "error",
            message:
              "Server Unreachable: Cannot connect to Nojoin Backend API.",
            persistent: true,
          });
        }
      } else if (backend && notificationIds.current.backend) {
        removeActiveNotification(notificationIds.current.backend);
        notificationIds.current.backend = null;
      }

      // DB (only if backend is up)
      if (backend && !db && !notificationIds.current.db) {
        notificationIds.current.db = addNotification({
          type: "error",
          message: "Database Error: Connection to PostgreSQL failed.",
          persistent: true,
        });
      } else if ((!backend || db) && notificationIds.current.db) {
        removeActiveNotification(notificationIds.current.db);
        notificationIds.current.db = null;
      }

      // Worker (only if backend is up)
      if (backend && !worker && !notificationIds.current.worker) {
        if (!isStartupRef.current) {
          notificationIds.current.worker = addNotification({
            type: "error",
            message: "Worker Offline: Background processing is paused.",
            persistent: true,
          });
        }
      } else if ((!backend || worker) && notificationIds.current.worker) {
        removeActiveNotification(notificationIds.current.worker);
        notificationIds.current.worker = null;
      }
    };

    updateNotifications();
  }, [
    backend,
    db,
    worker,
    backendFailCount,
    addNotification,
    removeActiveNotification,
  ]);

  return null;
}
