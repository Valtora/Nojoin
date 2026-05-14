"use client";

import { useEffect, useRef } from "react";
import { useNotificationStore } from "@/lib/notificationStore";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import { getCompanionSteadyStateGuidance } from "@/lib/companionSteadyState";

export default function ServiceStatusAlerts() {
  const { addNotification, removeActiveNotification } = useNotificationStore();
  const {
    backend,
    backendVersion,
    db,
    worker,
    companion,
    companionAuthenticated,
    companionLocalConnectionUnavailable,
    companionLocalHttpsStatus,
    companionStatus,
    companionVersion,
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
  const companionGuidance = getCompanionSteadyStateGuidance({
    companion,
    companionAuthenticated,
    companionLocalConnectionUnavailable,
    companionLocalHttpsStatus,
    companionStatus,
    backendVersion,
    companionVersion,
  });

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
          "Companion pairing ended for this Nojoin backend. Start a new pairing request from the Nojoin settings page if you still need local recording controls.",
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
      persistent = false,
    ) => {
      if (companionNotificationState.current === stateKey) {
        return;
      }

      clearCompanionNotification();
      notificationIds.current.companion = addNotification({
        type,
        message,
        persistent,
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
      } else if (
        companionGuidance.key === "local-browser-connection-unavailable"
      ) {
        showCompanionNotification(
          "local-browser-connection-unavailable",
          "warning",
          "Local browser connection unavailable. Quit and relaunch the Companion app to restore browser-side local controls.",
          true,
        );
      } else if (companionGuidance.key === "version-mismatch") {
        showCompanionNotification(
          "version-mismatch",
          "warning",
          "Version mismatch. Open Companion support and align versions before local control will work again.",
        );
      } else if (
        companionGuidance.key === "local-browser-connection-recovering"
      ) {
        showCompanionNotification(
          "local-browser-connection-recovering",
          "info",
          "Local browser connection recovering. Browser controls will refresh automatically when recovery finishes.",
        );
      } else if (
        !isStartupRef.current &&
        companionGuidance.key === "temporarily-disconnected" &&
        companionFailCount > 2
      ) {
        showCompanionNotification(
          companionLocalConnectionUnavailable
            ? "temporarily-disconnected-local-unavailable"
            : "temporarily-disconnected",
          "info",
          companionLocalConnectionUnavailable
            ? "Temporarily disconnected. This pairing stays valid while the browser reconnects to the local Companion."
            : "Temporarily disconnected. Existing pairing stays valid and will resync automatically when the Companion reconnects.",
        );
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
    companionLocalConnectionUnavailable,
    companionLocalHttpsStatus,
    companionGuidance.key,
    companionMonitoringEnabled,
    companionStatus,
    companionVersion,
    backendFailCount,
    backendVersion,
    companionFailCount,
    addNotification,
    removeActiveNotification,
  ]);

  return null;
}
