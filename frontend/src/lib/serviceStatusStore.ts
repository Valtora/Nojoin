import { create } from "zustand";

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

interface ServiceStatusState {
  backend: boolean;
  db: boolean;
  worker: boolean;
  companion: boolean;
  companionAuthenticated: boolean;
  companionMonitoringEnabled: boolean;

  // Companion details
  companionStatus: "idle" | "recording" | "paused" | "error";
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
  enableCompanionMonitoring: () => void;
  pairCompanion: (pairingCode: string) => Promise<boolean>;
  triggerCompanionUpdate: () => Promise<boolean>;
  startPolling: () => void;
  stopPolling: () => void;
}

const BACKOFF_DELAYS = [1000, 2000, 4000, 8000, 16000, 32000, 60000];
const NORMAL_INTERVAL = 10000;
const COMPANION_URL = "http://127.0.0.1:12345";

export const useServiceStatusStore = create<ServiceStatusState>((set, get) => {
  let backendTimer: NodeJS.Timeout | null = null;
  let companionTimer: NodeJS.Timeout | null = null;

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
    if (!get().isPolling || !get().companionMonitoringEnabled) return;

    const failCount = get().companionFailCount;
    const delay =
      failCount === 0
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
      if (!get().companionMonitoringEnabled) {
        return;
      }

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);

        const res = await fetch(`${COMPANION_URL}/status`, {
          signal: controller.signal,
          method: "GET",
        });
        clearTimeout(timeoutId);

        if (res.ok) {
          const data: CompanionStatusResponse = await res.json();
          let status: "idle" | "recording" | "paused" | "error" = "idle";
          let duration = 0;

          if (typeof data === "object" && data.status) {
            let s = "";
            if (typeof data.status === "string") {
              s = data.status.toLowerCase();
            } else if (typeof data.status === "object") {
              s = Object.keys(data.status)[0].toLowerCase();
            }

            if (s === "idle") status = "idle";
            else if (s === "recording") status = "recording";
            else if (s === "paused") status = "paused";

            if (typeof data.duration_seconds === "number") {
              duration = data.duration_seconds;
            }
          }

          // Check if re-authorization is needed due to host mismatch
          let isAuthenticated = data.authenticated === true;
          if (isAuthenticated && data.api_host) {
            const currentHost = window.location.hostname;
            const isLocal = (h: string) =>
              h === "localhost" || h === "127.0.0.1";

            // Companion expected local if current is local.
            // If current is remote, companion must match remote
            if (isLocal(currentHost)) {
              if (!isLocal(data.api_host)) isAuthenticated = false;
            } else {
              if (data.api_host !== currentHost) isAuthenticated = false;
            }
          }

          set({
            companion: true,
            companionAuthenticated: isAuthenticated,
            companionStatus: status,
            companionVersion: data.version || null,
            companionUpdateAvailable: data.update_available || false,
            companionLatestVersion: data.latest_version || null,
            recordingDuration: duration,
            companionFailCount: 0,
          });
        } else {
          set((state) => ({
            companion: false,
            companionAuthenticated: false,
            companionUpdateAvailable: false,
            companionFailCount: state.companionFailCount + 1,
          }));
        }
      } catch {
        set((state) => ({
          companion: false,
          companionAuthenticated: false,
          companionUpdateAvailable: false,
          companionFailCount: state.companionFailCount + 1,
        }));
      }
      scheduleNextCompanion();
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

      try {
        const apiBase = process.env.NEXT_PUBLIC_API_URL
          ? `${process.env.NEXT_PUBLIC_API_URL}/v1`
          : "https://localhost:14443/api/v1";
        const tokenRes = await fetch(`${apiBase}/login/companion-token`, {
          credentials: "include",
        });
        if (!tokenRes.ok) {
          throw new Error(`Failed to fetch companion token: ${tokenRes.status}`);
        }
        const { token } = await tokenRes.json();

        const fpRes = await fetch(`${apiBase}/system/fingerprint`, {
          credentials: "include",
        });
        let tls_fingerprint = null;
        if (fpRes.ok) {
           const fpData = await fpRes.json();
           tls_fingerprint = fpData.fingerprint;
        }

        const api_protocol = window.location.protocol.replace(":", "");
        const api_host = window.location.hostname;
        let api_port = parseInt(
          window.location.port ||
            (window.location.protocol === "https:" ? "443" : "80"),
        );

        if (
          (api_host === "localhost" || api_host === "127.0.0.1") &&
          api_port === 3000
        ) {
          api_port = 14443;
        }

        const res = await fetch(`${COMPANION_URL}/pair/complete`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pairing_code: trimmedCode,
            bootstrap_token: token,
            api_protocol,
            api_host,
            api_port,
            tls_fingerprint,
          }),
        });

        const data = await res.json().catch(() => null);

        if (!res.ok) {
          throw new Error(
            data?.message || `Companion app returned ${res.status}`,
          );
        }

        if (!data?.success) {
          throw new Error(data?.message || "Pairing failed.");
        }

        set({
          companion: true,
          companionAuthenticated: true,
          companionMonitoringEnabled: true,
          companionFailCount: 0,
        });
        await get().checkCompanion();
        return true;
      } catch (e: any) {
        console.error("Failed to pair companion:", e);
        throw e;
      }
    },

    triggerCompanionUpdate: async (): Promise<boolean> => {
      try {
        const res = await fetch(`${COMPANION_URL}/update`, {
          method: "POST",
        });
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
      get().checkBackend();
      if (get().companionMonitoringEnabled) {
        void get().checkCompanion();
      }
    },

    stopPolling: () => {
      set({ isPolling: false });
      if (backendTimer) clearTimeout(backendTimer);
      if (companionTimer) clearTimeout(companionTimer);
    },
  };
});
