import { create } from "zustand";
import type { DeploymentWarning } from "@/types";

interface DetailedHealthStatus {
  status: string;
  version: string;
  deployment_warnings: DeploymentWarning[];
  components: {
    db: string;
    worker: string;
  };
}

interface ServiceStatusState {
  backend: boolean;
  db: boolean;
  worker: boolean;
  backendVersion: string | null;
  deploymentWarnings: DeploymentWarning[];
  isPolling: boolean;
  backendFailCount: number;
  checkBackend: () => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

const BACKOFF_DELAYS = [1000, 2000, 4000, 8000, 16000, 32000, 60000];
const NORMAL_INTERVAL = 10000;
const BACKEND_REQUEST_TIMEOUT_MS = 5000;

export const useServiceStatusStore = create<ServiceStatusState>((set, get) => {
  let backendTimer: ReturnType<typeof setTimeout> | null = null;

  const clearBackendTimer = () => {
    if (!backendTimer) {
      return;
    }

    clearTimeout(backendTimer);
    backendTimer = null;
  };

  const scheduleNextBackend = () => {
    if (!get().isPolling) {
      return;
    }

    const failCount = get().backendFailCount;
    const delay =
      failCount === 0
        ? NORMAL_INTERVAL
        : BACKOFF_DELAYS[Math.min(failCount - 1, BACKOFF_DELAYS.length - 1)];

    clearBackendTimer();
    backendTimer = setTimeout(() => {
      void get().checkBackend();
    }, delay);
  };

  const markBackendUnavailable = () => {
    set((state) => ({
      backend: false,
      db: false,
      worker: false,
      deploymentWarnings: [],
      backendFailCount: state.backendFailCount + 1,
    }));
  };

  return {
    backend: true,
    db: true,
    worker: true,
    backendVersion: null,
    deploymentWarnings: [],
    isPolling: false,
    backendFailCount: 0,

    checkBackend: async () => {
      const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL || "/api").replace(
        /\/$/,
        "",
      );

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(
          () => controller.abort(),
          BACKEND_REQUEST_TIMEOUT_MS,
        );

        const response = await fetch(`${apiBaseUrl}/v1/system/health`, {
          signal: controller.signal,
          method: "GET",
          credentials: "include",
        });

        clearTimeout(timeoutId);

        if (response.ok) {
          const data: DetailedHealthStatus = await response.json();
          set({
            backend: true,
            db: data.components.db === "connected",
            worker: data.components.worker === "active",
            backendVersion: data.version,
            deploymentWarnings: data.deployment_warnings,
            backendFailCount: 0,
          });
          scheduleNextBackend();
          return;
        }
      } catch {
        // Fall back to the unauthenticated probe to distinguish backend
        // outages from failures in the richer health endpoint.
      }

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(
          () => controller.abort(),
          BACKEND_REQUEST_TIMEOUT_MS,
        );

        const response = await fetch(`${apiBaseUrl}/health`, {
          signal: controller.signal,
          method: "GET",
        });

        clearTimeout(timeoutId);

        if (response.ok) {
          set({
            backend: true,
            db: true,
            worker: true,
            backendFailCount: 0,
          });
        } else {
          markBackendUnavailable();
        }
      } catch {
        markBackendUnavailable();
      }

      scheduleNextBackend();
    },

    startPolling: () => {
      if (get().isPolling) {
        return;
      }

      set({ isPolling: true });
      void get().checkBackend();
    },

    stopPolling: () => {
      set({ isPolling: false });
      clearBackendTimer();
    },
  };
});
