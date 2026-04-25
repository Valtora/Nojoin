"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Download,
  Link2,
  RefreshCw,
} from "lucide-react";

import { CompanionDevices } from "@/types";
import {
  CompanionPairingError,
  useServiceStatusStore,
} from "@/lib/serviceStatusStore";
import { useNotificationStore } from "@/lib/notificationStore";
import { getCompanionReleases, type CompanionReleases } from "@/lib/api";
import { detectPlatform, getDownloadUrl } from "@/lib/platform";
import { fuzzyMatch } from "@/lib/searchUtils";

import AudioSettings from "./AudioSettings";
import { AUDIO_KEYWORDS, COMPANION_KEYWORDS } from "./keywords";

interface CompanionAppSettingsProps {
  companionConfig: {
    api_port: number;
    local_port: number;
    min_meeting_length?: number;
  } | null;
  onUpdateCompanionConfig: (config: {
    api_port?: number;
    min_meeting_length?: number;
  }) => void;
  onRefreshCompanionConfig?: () => Promise<boolean>;
  companionDevices: CompanionDevices | null;
  selectedInputDevice: string | null;
  onSelectInputDevice: (device: string | null) => void;
  selectedOutputDevice: string | null;
  onSelectOutputDevice: (device: string | null) => void;
  searchQuery?: string;
}

type PairingAttemptState =
  | "idle"
  | "code-required"
  | "expired"
  | "failed"
  | "blocked"
  | "unreachable";

type CalloutTone = "success" | "info" | "warning" | "error";

interface CalloutState {
  title: string;
  message: string;
  tone: CalloutTone;
}

const CALLOUT_STYLES: Record<CalloutTone, string> = {
  success:
    "border-green-200 bg-green-50 text-green-800 dark:border-green-500/20 dark:bg-green-500/10 dark:text-green-200",
  info:
    "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-200",
  warning:
    "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200",
  error:
    "border-red-200 bg-red-50 text-red-800 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200",
};

const buildConnectionSummary = (
  backendVersion: string | null,
  companion: boolean,
  companionAuthenticated: boolean,
  companionStatus: string,
  companionVersion: string | null,
): CalloutState => {
  if (companionAuthenticated && backendVersion && companionVersion && backendVersion !== companionVersion) {
    return {
      title: "Version Mismatch",
      message: `Your Companion version (${companionVersion}) does not match the Nojoin server version (${backendVersion}). A mandatory re-pair is required after updating the Companion app to match the server version.`,
      tone: "error",
    };
  }

  if (!companionAuthenticated) {
    return {
      title: "Not paired",
      message:
        "This Nojoin deployment does not have an active Companion pairing yet. Start pairing from Companion Settings, then enter the code below.",
      tone: "info",
    };
  }

  if (!companion) {
    return {
      title: "Temporarily disconnected",
      message:
        "The Companion pairing is still valid. Recording timers, waveform state, and local controls will resync when the app reconnects.",
      tone: "warning",
    };
  }

  if (companionStatus === "recording" || companionStatus === "paused") {
    return {
      title: "Connected, recording active",
      message:
        "This deployment is already connected to the Companion. Backend switching stays blocked while a recording is active.",
      tone: "warning",
    };
  }

  if (companionStatus === "uploading") {
    return {
      title: "Connected, upload in progress",
      message:
        "This deployment is already connected to the Companion. Backend switching stays blocked until the current upload finishes.",
      tone: "warning",
    };
  }

  if (companionStatus === "backend-offline") {
    return {
      title: "Connected, backend offline",
      message:
        "The Companion pairing is intact, but Nojoin is offline right now. Existing uploads will resume after reconnect.",
      tone: "info",
    };
  }

  if (companionStatus === "error") {
    return {
      title: "Companion needs attention",
      message:
        "The Companion remains paired to this deployment, but it reported an error. Open Companion Settings for details.",
      tone: "warning",
    };
  }

  return {
    title: "Connected",
    message:
      "The Companion is paired to this Nojoin deployment and ready for local recording controls.",
    tone: "success",
  };
};

const buildPairingSummary = (
  pairingAttemptState: PairingAttemptState,
  pairingHasPendingConflict: boolean,
): CalloutState => {
  if (pairingAttemptState === "expired") {
    return {
      title: "Pairing expired",
      message:
        "The last pairing code expired or the Companion pairing window closed. Generate a new code from Companion Settings and try again.",
      tone: "warning",
    };
  }

  if (pairingAttemptState === "failed") {
    return {
      title: pairingHasPendingConflict ? "Previous request still pending" : "Pairing failed",
      message: pairingHasPendingConflict
        ? "Cancel the previous pending request or enter the current Companion code before retrying."
        : "The submitted pairing code was rejected. Verify the current code from Companion Settings and try again.",
      tone: pairingHasPendingConflict ? "warning" : "error",
    };
  }

  if (pairingAttemptState === "blocked") {
    return {
      title: "Pairing blocked",
      message:
        "The Companion is still recording or uploading on another deployment. Stop the active work there before switching this machine to a new backend.",
      tone: "warning",
    };
  }

  if (pairingAttemptState === "unreachable") {
    return {
      title: "Companion unreachable",
      message:
        "No local Companion response was received. Start the app, reopen pairing from Companion Settings, then retry.",
      tone: "error",
    };
  }

  return {
    title: "Pairing code required",
    message:
      "Open Companion Settings, choose Pair with Nojoin, and enter the current 8-character code below. Pairing codes expire quickly if the Companion window closes.",
    tone: "info",
  };
};

const resolvePairingFailure = (
  error: unknown,
): {
  state: PairingAttemptState;
  message: string;
  notificationType: "error" | "warning" | "info";
} => {
  if (error instanceof TypeError && error.message === "Failed to fetch") {
    return {
      state: "unreachable",
      message:
        "Companion app is unreachable. Start it, reopen pairing from Companion Settings, then try again.",
      notificationType: "error",
    };
  }

  if (error instanceof CompanionPairingError) {
    const message = error.message || "Failed to pair with Companion App.";
    const lowerMessage = message.toLowerCase();

    if (error.status === 410 || lowerMessage.includes("expired")) {
      return {
        state: "expired",
        message:
          "The pairing code expired. Generate a new code from Companion Settings and try again.",
        notificationType: "warning",
      };
    }

    if (
      error.status === 409 &&
      lowerMessage.includes("pairing is unavailable while")
    ) {
      return {
        state: "blocked",
        message: lowerMessage.includes("upload")
          ? "Pairing is blocked until the current upload finishes. Wait for the Companion to return to idle, then try again."
          : "Pairing is blocked while a recording is active on another deployment. Stop the recording before switching this machine to a new backend.",
        notificationType: "warning",
      };
    }

    if (error.status === 403 && lowerMessage.includes("not active")) {
      return {
        state: "code-required",
        message:
          "Companion pairing mode is not active. Open Pair with Nojoin in Companion Settings and enter the current code.",
        notificationType: "info",
      };
    }

    if (error.status === 409 && lowerMessage.includes("still pending")) {
      return {
        state: "failed",
        message:
          "A previous pairing attempt is still pending. Cancel the earlier request and retry with the current Companion code.",
        notificationType: "warning",
      };
    }

    if (error.status === 409 && lowerMessage.includes("already in progress")) {
      return {
        state: "failed",
        message:
          "Pairing confirmation is already running. Wait a few seconds for the current request to finish before retrying.",
        notificationType: "info",
      };
    }

    if (error.status === 429) {
      return {
        state: "failed",
        message:
          "Too many invalid pairing attempts were made. Generate a fresh Companion code and try again.",
        notificationType: "error",
      };
    }

    if (error.status === 401) {
      return {
        state: "failed",
        message:
          "Pairing could not be confirmed because the backend bootstrap token expired. Generate a new code and retry.",
        notificationType: "error",
      };
    }

    if (lowerMessage.includes("invalid")) {
      return {
        state: "failed",
        message:
          "The pairing code was rejected. Verify the current code from Companion Settings and try again.",
        notificationType: "error",
      };
    }

    return {
      state: "failed",
      message,
      notificationType: error.status === 409 ? "warning" : "error",
    };
  }

  if (error instanceof Error && error.message) {
    return {
      state: "failed",
      message: error.message,
      notificationType: "error",
    };
  }

  return {
    state: "failed",
    message: "Failed to pair with Companion App.",
    notificationType: "error",
  };
};

const formatPairingCode = (value: string) => {
  const canonical = value
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, 8);

  if (canonical.length <= 4) {
    return canonical;
  }

  return `${canonical.slice(0, 4)}-${canonical.slice(4)}`;
};

export default function CompanionAppSettings({
  companionConfig,
  onUpdateCompanionConfig,
  onRefreshCompanionConfig,
  companionDevices,
  selectedInputDevice,
  onSelectInputDevice,
  selectedOutputDevice,
  onSelectOutputDevice,
  searchQuery = "",
}: CompanionAppSettingsProps) {
  const {
    backendVersion,
    companion,
    companionAuthenticated,
    companionStatus,
    companionVersion,
    companionUpdateAvailable,
    checkCompanion,
    pairCompanion,
    cancelPendingCompanionPairing,
    triggerCompanionUpdate,
  } = useServiceStatusStore();
  const { addNotification } = useNotificationStore();
  const [companionReleases, setCompanionReleases] =
    useState<CompanionReleases | null>(null);
  const [pairingCode, setPairingCode] = useState("");
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [pairingNotice, setPairingNotice] = useState<string | null>(null);
  const [pairingAttemptState, setPairingAttemptState] =
    useState<PairingAttemptState>("idle");
  const [isPairingCompanion, setIsPairingCompanion] = useState(false);
  const [isCancellingPendingPairing, setIsCancellingPendingPairing] =
    useState(false);
  const [isTriggeringUpdate, setIsTriggeringUpdate] = useState(false);
  const pairingRequestInFlightRef = useRef(false);
  const refreshCompanionConfigRef = useRef(onRefreshCompanionConfig);

  useEffect(() => {
    refreshCompanionConfigRef.current = onRefreshCompanionConfig;
  }, [onRefreshCompanionConfig]);

  const synchronizeSuccessfulPairing = async () => {
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const latestStoreState = useServiceStatusStore.getState();
      if (!latestStoreState.companionAuthenticated) {
        await latestStoreState.checkCompanion();
      }

      if (useServiceStatusStore.getState().companionAuthenticated) {
        const refreshed =
          (await refreshCompanionConfigRef.current?.()) ?? true;
        if (refreshed) {
          return true;
        }
      }

      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, 250);
      });
    }

    return useServiceStatusStore.getState().companionAuthenticated;
  };

  useEffect(() => {
    const fetchReleases = async () => {
      try {
        const releases = await getCompanionReleases();
        setCompanionReleases(releases);
      } catch (error) {
        console.error("Failed to fetch companion releases:", error);
      }
    };

    void fetchReleases();
  }, []);

  useEffect(() => {
    const refreshCompanionState = () => {
      if (document.visibilityState !== "visible") {
        return;
      }

      void checkCompanion();
      if (companionAuthenticated) {
        void onRefreshCompanionConfig?.();
      }
    };

    window.addEventListener("focus", refreshCompanionState);
    document.addEventListener("visibilitychange", refreshCompanionState);

    return () => {
      window.removeEventListener("focus", refreshCompanionState);
      document.removeEventListener("visibilitychange", refreshCompanionState);
    };
  }, [checkCompanion, companionAuthenticated, onRefreshCompanionConfig]);

  useEffect(() => {
    if (!companionAuthenticated || companion) {
      return;
    }

    const revalidateCompanionPairing = () => {
      if (document.visibilityState !== "visible") {
        return;
      }

      void checkCompanion();
      void onRefreshCompanionConfig?.();
    };

    revalidateCompanionPairing();
    const intervalId = window.setInterval(revalidateCompanionPairing, 1500);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [
    checkCompanion,
    companion,
    companionAuthenticated,
    onRefreshCompanionConfig,
  ]);

  useEffect(() => {
    if (!companionAuthenticated) {
      return;
    }

    setPairingAttemptState("idle");
    setPairingCode("");
    setPairingError(null);
    setPairingNotice(null);
    void onRefreshCompanionConfig?.();
  }, [companionAuthenticated, onRefreshCompanionConfig]);

  const showOverview = !searchQuery || fuzzyMatch(searchQuery, COMPANION_KEYWORDS);
  const showActions =
    !searchQuery ||
    fuzzyMatch(searchQuery, [
      "download",
      "installer",
      "update",
      "updates",
      "version",
      "pair",
      "pairing",
      "companion",
    ]);
  const showAudioSections = !searchQuery || fuzzyMatch(searchQuery, AUDIO_KEYWORDS);

  if (searchQuery && !showOverview && !showActions && !showAudioSections) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  const pairingHasPendingConflict =
    pairingError?.toLowerCase().includes("still pending") ?? false;
  const connectionSummary = buildConnectionSummary(
    backendVersion,
    companion,
    companionAuthenticated,
    companionStatus,
    companionVersion,
  );
  const pairingSummary = buildPairingSummary(
    pairingAttemptState,
    pairingHasPendingConflict,
  );

  const handleDownloadCompanion = () => {
    const platform = detectPlatform();

    if (platform === "windows" && companionReleases?.windows_url) {
      window.open(companionReleases.windows_url, "_blank");
      return;
    }

    const downloadUrl = getDownloadUrl();
    window.open(downloadUrl, "_blank");
  };

  const handlePairCompanion = async (
    event?: FormEvent<HTMLFormElement>,
  ) => {
    event?.preventDefault();

    if (pairingCode.replace(/[^A-Z0-9]/g, "").length !== 8) {
      return;
    }

    if (pairingRequestInFlightRef.current) {
      return;
    }

    pairingRequestInFlightRef.current = true;
    setIsPairingCompanion(true);
    setPairingError(null);
    setPairingNotice(null);
    try {
      await pairCompanion(pairingCode);
      await synchronizeSuccessfulPairing();
      setPairingAttemptState("idle");
      addNotification({
        type: "success",
        message:
          "Companion paired successfully. Local recording controls are ready on this Nojoin deployment.",
      });
    } catch (error) {
      const failure = resolvePairingFailure(error);
      setPairingAttemptState(failure.state);
      setPairingError(failure.message);
      addNotification({
        type: failure.notificationType,
        message: failure.message,
      });
    } finally {
      pairingRequestInFlightRef.current = false;
      setIsPairingCompanion(false);
    }
  };

  const handleCancelPendingPairing = async () => {
    setIsCancellingPendingPairing(true);
    setPairingNotice(null);

    try {
      await cancelPendingCompanionPairing();
      setPairingAttemptState("code-required");
      setPairingError(null);
      setPairingNotice(
        "Previous pending pairing request cancelled. Enter the current Companion code and try again.",
      );
      addNotification({
        type: "info",
        message:
          "Previous pending pairing request cancelled. Enter the current Companion code and try again.",
      });
    } catch (error: unknown) {
      const message =
        error instanceof Error
          ? error.message
          : "Failed to cancel the previous pending pairing request.";
      setPairingError(message);
      addNotification({
        type: "error",
        message,
      });
    } finally {
      setIsCancellingPendingPairing(false);
    }
  };

  const handleUpdateCompanion = async () => {
    setIsTriggeringUpdate(true);
    try {
      const triggered = await triggerCompanionUpdate();
      addNotification({
        type: triggered ? "success" : "error",
        message: triggered
          ? "Companion update requested. Follow the Companion prompts to finish installation."
          : "Failed to trigger the Companion update.",
      });
    } finally {
      setIsTriggeringUpdate(false);
    }
  };

  return (
    <div className="space-y-8">
      {showOverview && (
        <section className="space-y-4">
          <div>
            <h3 className="text-lg font-medium text-gray-900 dark:text-white">
              Companion Connection
            </h3>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              Manage manual pairing and confirm the current local connection state.
            </p>
          </div>

          <div className={`rounded-xl border px-4 py-4 ${CALLOUT_STYLES[connectionSummary.tone]}`}>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em]">
              {connectionSummary.title}
            </div>
            <p className="mt-2 text-sm leading-6">{connectionSummary.message}</p>
            <div className="mt-3 flex flex-wrap gap-3 text-xs opacity-90">
              <span>Companion version: {companionVersion || "Unavailable"}</span>
              <span>Local port: {companionConfig?.local_port || 12345}</span>
            </div>
          </div>

          {!companionAuthenticated && (
            <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-900">
              <div className="flex items-start gap-3">
                <div className="rounded-full bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-200">
                  <Link2 className="h-4 w-4" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-900 dark:text-white">
                    Pair Companion
                  </p>
                  <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                    Open the Nojoin Companion app, choose Pair with Nojoin in Companion Settings, and enter the displayed 8-character code here.
                  </p>
                </div>
              </div>

              <div className={`mt-4 rounded-xl border px-4 py-3 ${CALLOUT_STYLES[pairingSummary.tone]}`}>
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em]">
                  {pairingSummary.title}
                </div>
                <p className="mt-1 text-sm leading-6">{pairingSummary.message}</p>
              </div>

              <form
                className="mt-4 space-y-3"
                onSubmit={(event) => void handlePairCompanion(event)}
              >
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Pairing code
                </label>
                <input
                  type="text"
                  value={pairingCode}
                  onChange={(event) =>
                    setPairingCode(formatPairingCode(event.target.value))
                  }
                  placeholder="ABCD-EFGH"
                  autoFocus
                  className="w-full rounded-xl border border-gray-300 bg-white px-4 py-3 text-center font-mono text-xl font-semibold uppercase tracking-[0.22em] text-gray-950 outline-none transition focus:border-orange-500 focus:ring-2 focus:ring-orange-500/30 dark:border-gray-700 dark:bg-gray-950 dark:text-white"
                />
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Pairing codes are short-lived and expire quickly if the Companion window closes.
                </p>
                {pairingError && (
                  <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
                    {pairingError}
                  </div>
                )}

                {pairingNotice && (
                  <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-300">
                    {pairingNotice}
                  </div>
                )}

                <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:justify-end">
                  {pairingHasPendingConflict && (
                    <button
                      type="button"
                      onClick={() => void handleCancelPendingPairing()}
                      disabled={isPairingCompanion || isCancellingPendingPairing}
                      className="rounded-xl border border-orange-300 px-4 py-3 text-sm font-semibold text-orange-700 transition hover:border-orange-400 hover:bg-orange-50 disabled:cursor-not-allowed disabled:border-orange-200 disabled:text-orange-300 dark:border-orange-500/30 dark:text-orange-300 dark:hover:bg-orange-500/10 dark:disabled:border-orange-500/20 dark:disabled:text-orange-500/50"
                    >
                      {isCancellingPendingPairing
                        ? "Cancelling Pending..."
                        : "Cancel Previous Request"}
                    </button>
                  )}
                  <button
                    type="submit"
                    disabled={
                      isPairingCompanion ||
                      isCancellingPendingPairing ||
                      pairingCode.replace(/[^A-Z0-9]/g, "").length !== 8
                    }
                    className="rounded-xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 dark:disabled:bg-orange-900/40"
                  >
                    {isPairingCompanion ? "Pairing..." : "Complete Pairing"}
                  </button>
                </div>
              </form>
            </div>
          )}
        </section>
      )}

      {showActions && (
        <section className="space-y-4">
          <div>
            <h3 className="text-lg font-medium text-gray-900 dark:text-white">
              Companion Actions
            </h3>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              Download the installer, or trigger an in-app update when the local Companion reports one.
            </p>
          </div>

          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={handleDownloadCompanion}
              className="inline-flex items-center gap-2 rounded-xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-orange-700"
            >
              <Download className="h-4 w-4" />
              Download Companion
            </button>
            {companion && companionUpdateAvailable && (
              <button
                type="button"
                onClick={() => void handleUpdateCompanion()}
                disabled={isTriggeringUpdate}
                className="inline-flex items-center gap-2 rounded-xl border border-blue-300 bg-blue-50 px-4 py-3 text-sm font-semibold text-blue-800 transition hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200 dark:hover:bg-blue-500/20"
              >
                {isTriggeringUpdate ? (
                  <RefreshCw className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                Update Companion
              </button>
            )}
          </div>
        </section>
      )}

      {showAudioSections && (
        <section className="space-y-4">
          <div>
            <h3 className="text-lg font-medium text-gray-900 dark:text-white">
              Recording Preferences
            </h3>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              Manage local device selection, minimum meeting length, and recording-time warning preferences for the Companion app.
            </p>
          </div>

          <AudioSettings
            companionConfig={companionConfig}
            onUpdateCompanionConfig={onUpdateCompanionConfig}
            onRefreshCompanionConfig={onRefreshCompanionConfig}
            companionDevices={companionDevices}
            selectedInputDevice={selectedInputDevice}
            onSelectInputDevice={onSelectInputDevice}
            selectedOutputDevice={selectedOutputDevice}
            onSelectOutputDevice={onSelectOutputDevice}
            searchQuery={searchQuery}
            suppressNoMatch
          />
        </section>
      )}

      {!companionAuthenticated && pairingAttemptState === "blocked" && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <p>
              Companion pairing cannot switch backends while recording or upload work is still active on the current deployment.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
