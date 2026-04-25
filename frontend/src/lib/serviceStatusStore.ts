import { create } from "zustand";
import {
  CompanionLocalRequestError,
  companionLocalFetch,
  COMPANION_URL,
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
}

interface CompanionPairingPayload {
  pairing_code: string;
  companion_credential_secret: string;
  api_protocol: string;
  api_host: string;
  api_port: number;
  tls_fingerprint?: string | null;
  local_control_secret: string;
  local_control_secret_version: number;
  backend_pairing_id: string;
}

interface CompanionPairingErrorResponse {
  detail?: string;
  message?: string;
  error?: string;
}

export type CompanionRuntimeStatus =
  | "idle"
  | "recording"
  | "paused"
  | "uploading"
  | "backend-offline"
  | "error";

export class CompanionPairingError extends Error {
  status?: number;
  phase: "prepare" | "complete" | "cancel";

  constructor(
    message: string,
    status: number | undefined,
    phase: "prepare" | "complete" | "cancel",
  ) {
    super(message);
    this.name = "CompanionPairingError";
    this.status = status;
    this.phase = phase;
  }
}

interface ServiceStatusState {
  backend: boolean;
  db: boolean;
  worker: boolean;
  backendVersion: string | null;
  companion: boolean;
  companionAuthenticated: boolean;
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
  pairCompanion: (pairingCode: string) => Promise<boolean>;
  cancelPendingCompanionPairing: () => Promise<boolean>;
  triggerCompanionUpdate: () => Promise<boolean>;
  startPolling: () => void;
  stopPolling: () => void;
}

const BACKOFF_DELAYS = [1000, 2000, 4000, 8000, 16000, 32000, 60000];
const COMPANION_DISCONNECTED_RECHECK_INTERVAL = 1500;
const NORMAL_INTERVAL = 10000;
const getCompanionApiBase = () =>
  process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}/v1`
    : "https://localhost:14443/api/v1";
const getCompanionEventsUrl = () =>
  process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}/v1/login/companion-events`
    : "/api/v1/login/companion-events";

const readPairingError = (
  payload: CompanionPairingPayload | CompanionPairingErrorResponse | null,
  fallback: string,
) => {
  const pairingError = payload as CompanionPairingErrorResponse | null;
  return (
    pairingError?.detail || pairingError?.message || pairingError?.error || fallback
  );
};

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

const cancelPendingCompanionPairingRequest = async (apiBase: string) => {
  const response = await fetch(`${apiBase}/login/companion-pairing/pending`, {
    method: "DELETE",
    credentials: "include",
  });
  const responseBody = (await response.json().catch(
    () => null,
  )) as CompanionPairingErrorResponse | null;

  if (!response.ok) {
    throw new CompanionPairingError(
      readPairingError(
        responseBody,
        `Failed to cancel pending companion pairing: ${response.status}`,
      ),
      response.status,
      "cancel",
    );
  }

  return true;
};

const bestEffortCancelPendingCompanionPairing = async (apiBase: string) => {
  try {
    await cancelPendingCompanionPairingRequest(apiBase);
  } catch (error) {
    console.error("Failed to cancel pending companion pairing:", error);
  }
};

const frontendPairingRuntimeId =
  typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `pair-runtime-${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;

let frontendPairingRequestSequence = 0;

const nextFrontendPairingRequestId = () => {
  frontendPairingRequestSequence += 1;
  return `${frontendPairingRuntimeId}:${frontendPairingRequestSequence}:${Date.now().toString(36)}`;
};

const isPairingAlreadyInProgressError = (error: unknown) =>
  error instanceof CompanionPairingError &&
  error.phase === "complete" &&
  error.status === 409 &&
  error.message.toLowerCase().includes("already in progress");

const isPendingPairingPrepareConflict = (error: unknown) =>
  error instanceof CompanionPairingError &&
  error.phase === "prepare" &&
  error.status === 409 &&
  error.message.toLowerCase().includes("still pending");

export const useServiceStatusStore = create<ServiceStatusState>((set, get) => {
  let backendTimer: NodeJS.Timeout | null = null;
  let companionTimer: NodeJS.Timeout | null = null;
  let companionEventSource: EventSource | null = null;
  let pairingRequestPromise: Promise<boolean> | null = null;

  const handleCompanionPairingEndedState = () => {
    set((state) => ({
      companion: false,
      companionAuthenticated: false,
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
      companionState.companionAuthenticated && !companionState.companion
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

          set({
            companion: true,
            companionAuthenticated: data.authenticated === true,
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

    pairCompanion: async (pairingCode: string): Promise<boolean> => {
      const trimmedCode = pairingCode.trim().toUpperCase();
      if (!trimmedCode) {
        throw new Error("Pairing code is required.");
      }

      if (pairingRequestPromise) {
        return pairingRequestPromise;
      }

      pairingRequestPromise = (async () => {
        const apiBase = getCompanionApiBase();
        let pairingPrepared = false;
        const frontendPairingRequestId = nextFrontendPairingRequestId();

        const preparePairing = async () => {
          const pairingRes = await fetch(`${apiBase}/login/companion-pairing`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pairing_code: trimmedCode }),
          });
          const pairingPayload = (await pairingRes.json().catch(
            () => null,
          )) as CompanionPairingPayload | null;

          if (!pairingRes.ok) {
            throw new CompanionPairingError(
              readPairingError(
                pairingPayload,
                `Failed to prepare companion pairing: ${pairingRes.status}`,
              ),
              pairingRes.status,
              "prepare",
            );
          }

          return pairingPayload;
        };

        try {
          let pairingPayload: CompanionPairingPayload | null;

          try {
            pairingPayload = await preparePairing();
          } catch (error) {
            if (!isPendingPairingPrepareConflict(error)) {
              throw error;
            }

            console.info(
              "Cancelling stale pending companion pairing before retrying with the current code",
              {
                frontendPairingRuntimeId,
                frontendPairingRequestId,
                pairingCode: trimmedCode,
              },
            );
            await cancelPendingCompanionPairingRequest(apiBase);
            pairingPayload = await preparePairing();
          }

          pairingPrepared = true;

          console.info("Submitting companion pairing completion request", {
            frontendPairingRuntimeId,
            frontendPairingRequestId,
            backendPairingId: pairingPayload?.backend_pairing_id,
          });

          const res = await fetch(`${COMPANION_URL}/pair/complete`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Nojoin-Frontend-Runtime": frontendPairingRuntimeId,
              "X-Nojoin-Frontend-Pair-Request": frontendPairingRequestId,
              "X-Nojoin-Frontend-Source": "settings-companion",
            },
            body: JSON.stringify(pairingPayload),
          });

          const data = await res.json().catch(() => null);

          if (!res.ok) {
            throw new CompanionPairingError(
              data?.message || `Companion app returned ${res.status}`,
              res.status,
              "complete",
            );
          }

          if (!data?.success) {
            throw new CompanionPairingError(
              data?.message || "Pairing failed.",
              res.status,
              "complete",
            );
          }

          set({
            companion: true,
            companionAuthenticated: true,
            companionMonitoringEnabled: true,
            companionFailCount: 0,
          });
          await get().checkCompanion();
          return true;
        } catch (error: unknown) {
          if (pairingPrepared && !isPairingAlreadyInProgressError(error)) {
            await bestEffortCancelPendingCompanionPairing(apiBase);
          }
          console.error("Failed to pair companion:", error);
          throw error;
        } finally {
          pairingRequestPromise = null;
        }
      })();

      return pairingRequestPromise;
    },

    cancelPendingCompanionPairing: async (): Promise<boolean> => {
      const apiBase = getCompanionApiBase();
      return cancelPendingCompanionPairingRequest(apiBase);
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
