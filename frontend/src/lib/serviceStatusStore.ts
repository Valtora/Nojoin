import { create } from "zustand";
import {
  CompanionLocalConnectionError,
  CompanionLocalRequestError,
  companionLocalFetch,
} from "@/lib/companionLocalApi";

interface DetailedHealthStatus {
  status: string;
  version: string;
  components: {
    db: string;
    worker: string;
  };
}

interface CompanionStatusResponse {
  status: string | { [key: string]: unknown };
  duration_seconds?: number;
  version?: string;
  authenticated?: boolean;
  api_host?: string;
  update_available?: boolean;
  latest_version?: string | null;
  localHttpsStatus?: CompanionLocalHttpsStatus;
}

export type CompanionRuntimeStatus =
  | "idle"
  | "recording"
  | "paused"
  | "uploading"
  | "backend-offline"
  | "error";

export type CompanionLocalHttpsStatus =
  | "ready"
  | "repairing"
  | "needs-repair";

interface ServiceStatusState {
  backend: boolean;
  db: boolean;
  worker: boolean;
  backendVersion: string | null;
  companion: boolean;
  companionAuthenticated: boolean;
  companionLocalConnectionUnavailable: boolean;
  companionLocalHttpsStatus: CompanionLocalHttpsStatus | null;
  companionMonitoringEnabled: boolean;

  // Companion details
  companionStatus: CompanionRuntimeStatus;
  companionVersion: string | null;
  companionUpdateAvailable: boolean;
  companionLatestVersion: string | null;
  recordingDuration: number;

  // Polling state
  isPolling: boolean;
  backendFailCount: number;
  companionFailCount: number;

  // Actions
  checkBackend: () => Promise<void>;
  checkCompanion: () => Promise<void>;
  handleCompanionPairingEnded: () => void;
  enableCompanionMonitoring: () => void;
  markCompanionPairingCompleted: () => Promise<void>;
  triggerCompanionUpdate: () => Promise<boolean>;
  startPolling: () => void;
  stopPolling: () => void;
}

const BACKOFF_DELAYS = [1000, 2000, 4000, 8000, 16000, 32000, 60000];
const COMPANION_DISCONNECTED_RECHECK_INTERVAL = 1500;
const COMPANION_REPAIRING_RECHECK_INTERVAL = 1500;
const COMPANION_NEEDS_REPAIR_RECHECK_INTERVAL = 60000;
const NORMAL_INTERVAL = 10000;
const getCompanionEventsUrl = () =>
  process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}/v1/login/companion-events`
    : "/api/v1/login/companion-events";

const readCompanionStatus = (
  payload: CompanionStatusResponse,
): { status: CompanionRuntimeStatus; duration: number } => {
  let status: CompanionRuntimeStatus = "idle";
  let duration = 0;

  if (typeof payload === "object" && payload.status) {
    let rawStatus = "";
    if (typeof payload.status === "string") {
      rawStatus = payload.status.toLowerCase();
    } else if (typeof payload.status === "object") {
      rawStatus = Object.keys(payload.status)[0]?.toLowerCase() || "";
    }

    if (rawStatus === "idle") status = "idle";
    else if (rawStatus === "recording") status = "recording";
    else if (rawStatus === "paused") status = "paused";
    else if (rawStatus === "uploading") status = "uploading";
    else if (rawStatus === "backendoffline") status = "backend-offline";
    else if (rawStatus === "error") status = "error";

    if (typeof payload.duration_seconds === "number") {
      duration = payload.duration_seconds;
    }
  }

  return { status, duration };
};

const readCompanionLocalHttpsStatus = (
  payload: CompanionStatusResponse,
): CompanionLocalHttpsStatus => {
  if (payload.localHttpsStatus === "repairing") {
    return "repairing";
  }

  if (payload.localHttpsStatus === "needs-repair") {
    return "needs-repair";
  }

  return "ready";
};


export const useServiceStatusStore = create<ServiceStatusState>((set, get) => {
  let backendTimer: NodeJS.Timeout | null = null;
  let companionTimer: NodeJS.Timeout | null = null;
  let companionEventSource: EventSource | null = null;

  const handleCompanionPairingEndedState = () => {
    set((state) => ({
      companion: false,
      companionAuthenticated: false,
      companionLocalConnectionUnavailable: false,
      companionLocalHttpsStatus: null,
      companionStatus: "idle",
      companionVersion: state.companionVersion,
      companionUpdateAvailable: false,
      companionLatestVersion: null,
      recordingDuration: 0,
      companionFailCount: 0,
    }));
  };

  const closeCompanionEvents = () => {
    if (!companionEventSource) {
      return;
    }

    companionEventSource.close();
    companionEventSource = null;
  };

  const openCompanionEvents = () => {
    if (typeof window === "undefined" || companionEventSource) {
      return;
    }

    const eventSource = new EventSource(getCompanionEventsUrl(), {
      withCredentials: true,
    });

    eventSource.addEventListener("companion-explicit-disconnect", () => {
      handleCompanionPairingEndedState();
      scheduleNextCompanion();
    });

    eventSource.onerror = () => {
      if (eventSource.readyState === EventSource.CLOSED) {
        if (companionEventSource === eventSource) {
          companionEventSource = null;
        }
        eventSource.close();
      }
    };

    companionEventSource = eventSource;
  };

  const scheduleNextBackend = () => {
    if (!get().isPolling) return;

    const failCount = get().backendFailCount;
    const delay =
      failCount === 0
        ? NORMAL_INTERVAL
        : BACKOFF_DELAYS[Math.min(failCount - 1, BACKOFF_DELAYS.length - 1)];

    if (backendTimer) clearTimeout(backendTimer);
    backendTimer = setTimeout(() => {
      get().checkBackend();
    }, delay);
  };

  const scheduleNextCompanion = () => {
    if (!get().isPolling) return;

    const companionState = get();
    const failCount = get().companionFailCount;
    const delay =
      companionState.companionLocalHttpsStatus === "needs-repair"
        ? COMPANION_NEEDS_REPAIR_RECHECK_INTERVAL
        : companionState.companionLocalHttpsStatus === "repairing"
        ? COMPANION_REPAIRING_RECHECK_INTERVAL
        : companionState.companionAuthenticated && !companionState.companion
        ? COMPANION_DISCONNECTED_RECHECK_INTERVAL
        : failCount === 0
        ? NORMAL_INTERVAL
        : BACKOFF_DELAYS[Math.min(failCount - 1, BACKOFF_DELAYS.length - 1)];

    if (companionTimer) clearTimeout(companionTimer);
    companionTimer = setTimeout(() => {
      get().checkCompanion();
    }, delay);
  };

  return {
    backend: true,
    db: true,
    worker: true,
    backendVersion: null,
    companion: false,
    companionAuthenticated: false,
    companionLocalConnectionUnavailable: false,
    companionLocalHttpsStatus: null,
    companionMonitoringEnabled: false,
    companionStatus: "idle",
    companionVersion: null,
    companionUpdateAvailable: false,
    companionLatestVersion: null,
    recordingDuration: 0,
    isPolling: false,
    backendFailCount: 0,
    companionFailCount: 0,

    checkBackend: async () => {
      const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "/api";
      const cleanBaseUrl = API_BASE_URL.replace(/\/$/, "");

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);

        const res = await fetch(`${cleanBaseUrl}/v1/system/health`, {
          signal: controller.signal,
          method: "GET",
          credentials: "include",
        });
        clearTimeout(timeoutId);

        if (res.ok) {
          const data: DetailedHealthStatus = await res.json();
          set({
            backend: true,
            db: data.components.db === "connected",
            worker: data.components.worker === "active",
            backendVersion: data.version,
            backendFailCount: 0,
          });
          scheduleNextBackend();
          return;
        }
      } catch {
        // Fall back to the minimal public health probe to distinguish
        // a backend outage from an authenticated telemetry failure.
      }

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);

        const res = await fetch(`${cleanBaseUrl}/health`, {
          signal: controller.signal,
          method: "GET",
        });
        clearTimeout(timeoutId);

        if (res.ok) {
          set({
            backend: true,
            db: true,
            worker: true,
            backendFailCount: 0,
          });
        } else {
          set((state) => ({
            backend: false,
            db: false,
            worker: false,
            backendFailCount: state.backendFailCount + 1,
          }));
        }
      } catch {
        set((state) => ({
          backend: false,
          db: false,
          worker: false,
          backendFailCount: state.backendFailCount + 1,
        }));
      }
      scheduleNextBackend();
    },

    checkCompanion: async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);

        const res = await companionLocalFetch(
          "/status",
          {
            signal: controller.signal,
            method: "GET",
          },
          "status:read",
        );
        clearTimeout(timeoutId);

        if (res.ok) {
          const data: CompanionStatusResponse = await res.json();
          const { status, duration } = readCompanionStatus(data);
          const localHttpsStatus = readCompanionLocalHttpsStatus(data);

          set({
            companion: true,
            companionAuthenticated: data.authenticated === true,
            companionLocalConnectionUnavailable: false,
            companionLocalHttpsStatus: localHttpsStatus,
            companionStatus: status,
            companionVersion: data.version || null,
            companionUpdateAvailable: data.update_available || false,
            companionLatestVersion: data.latest_version || null,
            recordingDuration: duration,
            companionFailCount: 0,
          });
        } else {
          const clearAuthentication = res.status === 403 || res.status === 409;
          if (clearAuthentication) {
            handleCompanionPairingEndedState();
          } else {
            set((state) => ({
              companion: false,
              companionAuthenticated: state.companionAuthenticated,
              companionLocalConnectionUnavailable: false,
              companionLocalHttpsStatus: null,
              companionUpdateAvailable: false,
              companionFailCount: state.companionFailCount + 1,
            }));
          }
        }
      } catch (error) {
        const clearAuthentication =
          error instanceof CompanionLocalRequestError &&
          (error.status === 403 || error.status === 409);
        if (clearAuthentication) {
          handleCompanionPairingEndedState();
        } else {
          set((state) => ({
            companion: false,
            companionAuthenticated: state.companionAuthenticated,
            companionLocalConnectionUnavailable:
              error instanceof CompanionLocalConnectionError,
            companionLocalHttpsStatus: null,
            companionUpdateAvailable: false,
            companionFailCount: state.companionFailCount + 1,
          }));
        }
      }
      scheduleNextCompanion();
    },

    handleCompanionPairingEnded: () => {
      handleCompanionPairingEndedState();
    },

    enableCompanionMonitoring: () => {
      if (get().companionMonitoringEnabled) {
        return;
      }

      set({ companionMonitoringEnabled: true });
      if (get().isPolling) {
        void get().checkCompanion();
      }
    },

    markCompanionPairingCompleted: async (): Promise<void> => {
      set({
        companion: true,
        companionAuthenticated: true,
        companionLocalConnectionUnavailable: false,
        companionLocalHttpsStatus: null,
        companionMonitoringEnabled: true,
        companionFailCount: 0,
      });
      await get().checkCompanion();
    },

    triggerCompanionUpdate: async (): Promise<boolean> => {
      try {
        const res = await companionLocalFetch(
          "/update",
          {
            method: "POST",
          },
          "update:trigger",
        );
        if (res.ok) {
          return true;
        }
        return false;
      } catch (e) {
        console.error("Failed to trigger update:", e);
        return false;
      }
    },

    startPolling: () => {
      if (get().isPolling) return;
      set({ isPolling: true });
      openCompanionEvents();
      void get().checkBackend();
      void get().checkCompanion();
    },

    stopPolling: () => {
      set({ isPolling: false });
      if (backendTimer) clearTimeout(backendTimer);
      if (companionTimer) clearTimeout(companionTimer);
      closeCompanionEvents();
    },
  };
});
