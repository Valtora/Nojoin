import { create } from "zustand";
import type { DeploymentWarning } from "@/types";
import { recordRequestOutcome } from "@/lib/connectivity/monitor";

interface DetailedHealthStatus {
  status: string;
  version: string;
  deployment_warnings: DeploymentWarning[];
  components: {
    db: string;
    worker: string;
  };
}

/**
 * Health *content* only: the subsystem status the backend reports about itself
 * (db, worker, version, deployment warnings). Backend *reachability* is a
 * separate concern owned by the connectivity monitor — a failure to load this
 * content must never assert "unreachable", it only means the content is stale.
 */
interface ServiceStatusState {
  db: boolean;
  worker: boolean;
  backendVersion: string | null;
  deploymentWarnings: DeploymentWarning[];
  isPolling: boolean;
  refreshHealth: () => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

const HEALTH_POLL_INTERVAL_MS = 15_000;
const HEALTH_REQUEST_TIMEOUT_MS = 8_000;

export const useServiceStatusStore = create<ServiceStatusState>((set, get) => {
  let healthTimer: ReturnType<typeof setTimeout> | null = null;

  const clearHealthTimer = () => {
    if (healthTimer) {
      clearTimeout(healthTimer);
      healthTimer = null;
    }
  };

  const scheduleNext = () => {
    if (!get().isPolling) {
      return;
    }
    clearHealthTimer();
    healthTimer = setTimeout(
      () => void get().refreshHealth(),
      HEALTH_POLL_INTERVAL_MS,
    );
  };

  return {
    db: true,
    worker: true,
    backendVersion: null,
    deploymentWarnings: [],
    isPolling: false,

    refreshHealth: async () => {
      // A hidden tab throttles timers and deprioritises fetches, so a probe can
      // falsely fail; stale content for a backgrounded tab is harmless. Skip
      // while hidden — the connectivity monitor owns reachability separately.
      if (
        typeof document !== "undefined" &&
        document.visibilityState === "hidden"
      ) {
        scheduleNext();
        return;
      }

      const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL || "/api").replace(
        /\/$/,
        "",
      );

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(
          () => controller.abort(),
          HEALTH_REQUEST_TIMEOUT_MS,
        );

        const response = await fetch(`${apiBaseUrl}/v1/system/health`, {
          signal: controller.signal,
          method: "GET",
          credentials: "include",
        });

        clearTimeout(timeoutId);

        // Any HTTP answer — even 401/500 — proves the backend is reachable,
        // independent of whether the payload was usable.
        recordRequestOutcome({ reachedServer: true });

        if (response.ok) {
          const data: DetailedHealthStatus = await response.json();
          set({
            db: data.components.db === "connected",
            worker: data.components.worker === "active",
            backendVersion: data.version,
            deploymentWarnings: data.deployment_warnings,
          });
        }
      } catch {
        // Transport failure: hand it to the connectivity monitor, which will
        // confirm with a dedicated probe. Keep the last-known content as-is.
        recordRequestOutcome({ reachedServer: false });
      }

      scheduleNext();
    },

    startPolling: () => {
      if (get().isPolling) {
        return;
      }

      set({ isPolling: true });
      void get().refreshHealth();
    },

    stopPolling: () => {
      set({ isPolling: false });
      clearHealthTimer();
    },
  };
});
