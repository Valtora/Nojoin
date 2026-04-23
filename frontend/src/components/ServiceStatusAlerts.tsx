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
    companion,
    companionAuthenticated,
    companionMonitoringEnabled,
    backendFailCount,
    companionFailCount,
    checkBackend,
    checkCompanion,
    startPolling,
    stopPolling,
  } = useServiceStatusStore();

  // Track active notification IDs
  const notificationIds = useRef<{ [key: string]: string | null }>({
    backend: null,
    db: null,
    worker: null,
    companion: null,
  });
  const companionNotificationState = useRef<string | null>(null);
  const previousCompanionAuthenticated = useRef(false);

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
      void checkCompanion();
    };

    window.addEventListener("focus", refreshStatuses);
    document.addEventListener("visibilitychange", refreshStatuses);

    return () => {
      window.removeEventListener("focus", refreshStatuses);
      document.removeEventListener("visibilitychange", refreshStatuses);
    };
  }, [checkBackend, checkCompanion]);

  useEffect(() => {
    if (
      !isStartupRef.current &&
      companionMonitoringEnabled &&
      previousCompanionAuthenticated.current &&
      !companionAuthenticated
    ) {
      addNotification({
        type: "info",
        message:
          "Companion pairing ended for this Nojoin backend. Start pairing again from Companion Settings if you still need local recording controls.",
      });
    }

    previousCompanionAuthenticated.current = companionAuthenticated;
  }, [
    addNotification,
    companionAuthenticated,
    companionMonitoringEnabled,
  ]);

  // Monitor Service Status
  useEffect(() => {
    const clearCompanionNotification = () => {
      if (notificationIds.current.companion) {
        removeActiveNotification(notificationIds.current.companion);
        notificationIds.current.companion = null;
      }
      companionNotificationState.current = null;
    };

    const showCompanionNotification = (
      stateKey: string,
      type: "error" | "warning" | "info",
      message: string,
    ) => {
      if (companionNotificationState.current === stateKey) {
        return;
      }

      clearCompanionNotification();
      notificationIds.current.companion = addNotification({
        type,
        message,
        persistent: true,
      });
      companionNotificationState.current = stateKey;
    };

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

      // Companion
      if (!companionMonitoringEnabled) {
        clearCompanionNotification();
      } else if (!isStartupRef.current && !companion && companionFailCount > 2) {
        if (companionAuthenticated) {
          showCompanionNotification(
            "paired-disconnected",
            "warning",
            "Companion temporarily disconnected. Existing pairing stays valid, and local recording state will resync when the app reconnects.",
          );
        } else {
          showCompanionNotification(
            "pairing-required",
            "info",
            "Companion pairing required. Start pairing from Companion Settings, then enter the code in Nojoin.",
          );
        }
      } else {
        clearCompanionNotification();
      }
    };

    updateNotifications();
  }, [
    backend,
    db,
    worker,
    companion,
    companionAuthenticated,
    companionMonitoringEnabled,
    backendFailCount,
    companionFailCount,
    addNotification,
    removeActiveNotification,
  ]);

  return null;
}
