import { create } from "zustand";

interface HealthStatus {
  status: string;
  version: string;
  components: {
    db: string;
    worker: string;
  };
}

interface AudioLevels {
  input_level: number;
  output_level: number;
  is_recording: boolean;
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

  // Companion details
  companionStatus: "idle" | "recording" | "paused" | "error";
  companionVersion: string | null;
  companionUpdateAvailable: boolean;
  companionLatestVersion: string | null;
  recordingDuration: number;

  // Audio levels
  audioLevels: {
    input: number;
    output: number;
  };

  // Polling state
  isPolling: boolean;
  backendFailCount: number;
  companionFailCount: number;

  // Actions
  checkBackend: () => Promise<void>;
  checkCompanion: () => Promise<void>;
  checkAudioLevels: () => Promise<void>;
  authorizeCompanion: () => Promise<boolean>;
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
  let audioTimer: NodeJS.Timeout | null = null;

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

  const scheduleNextAudio = () => {
    if (!get().isPolling) return;

    const { companion, companionStatus } = get();
    let delay = 2000;

    if (!companion) {
      delay = 10000;
    } else if (companionStatus !== "recording") {
      delay = 5000;
    } else {
      delay = 1000;
    }

    if (audioTimer) clearTimeout(audioTimer);
    audioTimer = setTimeout(() => {
      get().checkAudioLevels();
    }, delay);
  };

  return {
    backend: true,
    db: true,
    worker: true,
    companion: true,
    companionAuthenticated: false,
    companionStatus: "idle",
    companionVersion: null,
    companionUpdateAvailable: false,
    companionLatestVersion: null,
    recordingDuration: 0,
    audioLevels: { input: 0, output: 0 },
    isPolling: false,
    backendFailCount: 0,
    companionFailCount: 0,

    checkBackend: async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);

        // Use configured API URL or default to /api
        const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "/api";
        // Remove trailing slash if present to avoid double slashes
        const cleanBaseUrl = API_BASE_URL.replace(/\/$/, "");

        const res = await fetch(`${cleanBaseUrl}/health`, {
          signal: controller.signal,
          method: "GET",
        });
        clearTimeout(timeoutId);

        if (res.ok) {
          const data: HealthStatus = await res.json();
          set({
            backend: true,
            db: data.components.db === "connected",
            worker: data.components.worker === "active",
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

    checkAudioLevels: async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1000);

        const res = await fetch(`${COMPANION_URL}/levels`, {
          signal: controller.signal,
          method: "GET",
        });
        clearTimeout(timeoutId);

        if (res.ok) {
          const data: AudioLevels = await res.json();
          set({
            audioLevels: {
              input: data.input_level,
              output: data.output_level,
            },
          });
        }
      } catch {
        // Ignore audio level errors, handled by companion check
      }
      scheduleNextAudio();
    },

    authorizeCompanion: async (): Promise<boolean> => {
      try {
        // Get the current user's token from localStorage
        const token = localStorage.getItem("token");
        if (!token) {
          console.error("No auth token available");
          return false;
        }

        // Get current host and port to configure the companion app
        const api_host = window.location.hostname;
        let api_port = parseInt(
          window.location.port ||
            (window.location.protocol === "https:" ? "443" : "80"),
        );

        // Special handling for local development:
        // If accessing via localhost:3000 (Next.js dev server), point Companion to the standard Backend port (14443)
        // because the Companion requires HTTPS and the Backend is likely running in Docker on 14443.
        if (
          (api_host === "localhost" || api_host === "127.0.0.1") &&
          api_port === 3000
        ) {
          api_port = 14443;
        }

        const res = await fetch(`${COMPANION_URL}/auth`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            token,
            api_host,
            api_port,
          }),
        });

        if (res.ok) {
          const data = await res.json();
          if (data.success) {
            set({ companionAuthenticated: true });
            // Trigger a status check to update state
            get().checkCompanion();
            return true;
          }
        }
        return false;
      } catch (e) {
        console.error("Failed to authorize companion:", e);
        return false;
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
      get().checkCompanion();
      get().checkAudioLevels();
    },

    stopPolling: () => {
      set({ isPolling: false });
      if (backendTimer) clearTimeout(backendTimer);
      if (companionTimer) clearTimeout(companionTimer);
      if (audioTimer) clearTimeout(audioTimer);
    },
  };
});
