"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  Copy,
  Download,
  Link2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { CompanionDevices } from "@/types";
import {
  CompanionPairingError,
  type CompanionLocalHttpsStatus,
  type CompanionRuntimeStatus,
  useServiceStatusStore,
} from "@/lib/serviceStatusStore";
import { useNotificationStore } from "@/lib/notificationStore";
import { getCompanionReleases, type CompanionReleases } from "@/lib/api";
import {
  COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE,
  isCompanionLocalConnectionError,
} from "@/lib/companionLocalApi";
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

interface StatusCardState {
  status: string;
  message: string;
  tone: CalloutTone;
  primaryActionLabel: string;
  primaryActionMessage: string;
}

interface PairingCardState {
  title: string;
  message: string;
  helperText: string;
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

const SURFACE_CARD_STYLES =
  "rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-gray-950";

const FIREFOX_ENTERPRISE_ROOTS_PREF = "security.enterprise_roots.enabled";

const buildConnectionStateCard = ({
  backendVersion,
  companion,
  companionAuthenticated,
  companionLocalConnectionUnavailable,
  localHttpsStatus,
  companionStatus,
  companionVersion,
  showFirefoxSetupGuidance,
  pairingAttemptState,
  pairingError,
}: {
  backendVersion: string | null;
  companion: boolean;
  companionAuthenticated: boolean;
  companionLocalConnectionUnavailable: boolean;
  localHttpsStatus: CompanionLocalHttpsStatus | null;
  companionStatus: CompanionRuntimeStatus;
  companionVersion: string | null;
  showFirefoxSetupGuidance: boolean;
  pairingAttemptState: PairingAttemptState;
  pairingError: string | null;
}): StatusCardState => {
  const versionMismatch =
    companionAuthenticated &&
    backendVersion &&
    companionVersion &&
    backendVersion !== companionVersion;
  const pairingBlockedByUpload =
    pairingError?.toLowerCase().includes("upload") ?? false;

  if (localHttpsStatus === "needs-repair") {
    return {
      status: "Browser repair required",
      message:
        "Local browser controls are blocked until the Companion repair flow finishes.",
      tone: "warning",
      primaryActionLabel: "Open Settings to Repair",
      primaryActionMessage:
        "In the Companion app, open Settings and run the repair flow there before retrying pairing or local controls here.",
    };
  }

  if (versionMismatch) {
    return {
      status: "Version mismatch",
      message:
        "Nojoin and the Companion must be on compatible versions before local control will work again.",
      tone: "error",
      primaryActionLabel: "Open Settings",
      primaryActionMessage:
        "Use the Companion app to review update status. If the update clears trust state, generate a new pairing code after versions align.",
    };
  }

  if (pairingAttemptState === "blocked") {
    return pairingBlockedByUpload
      ? {
          status: "Backend switch blocked until upload finishes",
          message:
            "This machine cannot switch to this deployment until the current queued upload finishes.",
          tone: "warning",
          primaryActionLabel: "Open Settings",
          primaryActionMessage:
            "Use the Companion app to verify the active upload clears before you generate a new pairing code for this deployment.",
        }
      : {
          status: "Backend switch blocked while recording",
          message:
            "This machine cannot switch to this deployment while a recording is still active.",
          tone: "warning",
          primaryActionLabel: "Open Settings",
          primaryActionMessage:
            "Use the Companion app to stop or finish the active recording before you generate a new pairing code for this deployment.",
        };
  }

  if (showFirefoxSetupGuidance && !companionAuthenticated) {
    return {
      status: "Firefox setup incomplete",
      message:
        "Firefox pairing did not complete in this browser.",
      tone: "warning",
      primaryActionLabel: "Enable Firefox Support",
      primaryActionMessage:
        "In the Companion app, enable Firefox Support, turn on Firefox enterprise roots, restart Firefox, then generate a fresh pairing code.",
    };
  }

  if (pairingAttemptState === "expired") {
    return {
      status: "Pairing expired",
      message: "The last pairing code is no longer valid.",
      tone: "warning",
      primaryActionLabel: "Generate New Pairing Code",
      primaryActionMessage:
        "In the Companion app, open Settings and start a fresh pairing session. If this machine is replacing another backend, the current backend stays active until the new pairing succeeds.",
    };
  }

  if (!companionAuthenticated) {
    return {
      status: "Not paired",
      message: companionLocalConnectionUnavailable
        ? "This deployment is not paired to a Companion yet, and the browser cannot reach the local app right now."
        : "This deployment is not paired to a Companion yet.",
      tone: "info",
      primaryActionLabel: "Start Pairing",
      primaryActionMessage:
        "In the Companion app, open Settings and start pairing before you enter the current 8-character code in this browser.",
    };
  }

  if (!companion) {
    return {
      status: "Temporarily disconnected",
      message: companionLocalConnectionUnavailable
        ? "The pairing is still valid, but this browser cannot reach the local Companion right now."
        : "The pairing is still valid and should recover automatically when the Companion reconnects.",
      tone: "info",
      primaryActionLabel: "Open Settings",
      primaryActionMessage:
        "Use the Companion app only if the connection does not recover on its own after a short wait.",
    };
  }

  if (localHttpsStatus === "repairing") {
    return {
      status: "Browser repair in progress",
      message:
        "The Companion is repairing its local browser connection. This page will refresh automatically when the repair finishes.",
      tone: "info",
      primaryActionLabel: "Open Settings",
      primaryActionMessage:
        "Wait for this state to clear first. If it lingers, reopen the Companion app and check the native troubleshooting surface.",
    };
  }

  if (companionStatus === "recording" || companionStatus === "paused") {
    return {
      status: "Connected",
      message:
        "This deployment is paired and a recording is active locally.",
      tone: "success",
      primaryActionLabel: "Open Settings",
      primaryActionMessage:
        "Use the Companion app for native support actions. Recording preferences and device selection stay below on this page.",
    };
  }

  if (companionStatus === "uploading") {
    return {
      status: "Connected",
      message:
        "This deployment is paired and the Companion is still uploading work.",
      tone: "success",
      primaryActionLabel: "Open Settings",
      primaryActionMessage:
        "Use the Companion app for native support actions. Recording preferences and device selection stay below on this page.",
    };
  }

  if (companionStatus === "backend-offline") {
    return {
      status: "Connected",
      message:
        "The Companion pairing is intact, but the backend is offline right now.",
      tone: "info",
      primaryActionLabel: "Open Settings",
      primaryActionMessage:
        "Use the Companion app only if the backend comes back and local status still does not recover.",
    };
  }

  if (companionStatus === "error") {
    return {
      status: "Connected",
      message:
        "The Companion is still paired to this deployment, but it reported a local problem.",
      tone: "warning",
      primaryActionLabel: "Open Settings",
      primaryActionMessage:
        "Use the Companion app to review the local error before retrying browser-side controls or pairing work.",
    };
  }

  return {
    status: "Connected",
    message:
      "The Companion is paired to this deployment and ready for local recording controls.",
    tone: "success",
    primaryActionLabel: "Open Settings",
    primaryActionMessage:
      "Use the Companion app for native support actions. Recording preferences and device selection stay below on this page.",
  };
};

const buildPairingCardState = ({
  pairingAttemptState,
  isFirefoxBrowser,
  showFirefoxSetupGuidance,
}: {
  pairingAttemptState: PairingAttemptState;
  isFirefoxBrowser: boolean;
  showFirefoxSetupGuidance: boolean;
}): PairingCardState => {
  if (showFirefoxSetupGuidance) {
    return {
      title: "Enter pairing code",
      message:
        "After completing the Firefox support steps, enter a fresh 8-character code from the Companion pairing window.",
      helperText:
        "Use a fresh code after completing the Firefox support steps.",
    };
  }

  if (pairingAttemptState === "expired") {
    return {
      title: "Enter pairing code",
      message:
        "Enter a fresh 8-character code from the Companion pairing window.",
      helperText: "Codes expire when the pairing window closes.",
    };
  }

  return {
    title: "Enter pairing code",
    message:
      "Enter the current 8-character code from the Companion pairing window.",
    helperText: isFirefoxBrowser
      ? "Codes expire when the pairing window closes. If Firefox pairing fails, support steps will appear here."
      : "Codes expire when the pairing window closes.",
  };
};

const resolvePairingFailure = (
  error: unknown,
  isFirefoxBrowser: boolean,
): {
  state: PairingAttemptState;
  message: string;
  notificationType: "error" | "warning" | "info";
} => {
  if (isCompanionLocalConnectionError(error)) {
    return {
      state: "unreachable",
      message: isFirefoxBrowser
        ? "Firefox could not reach the local Companion. Enable Firefox Support in the Companion app, turn on Firefox enterprise roots, restart Firefox, then try again with a fresh code."
        : COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE,
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
          "The pairing code expired. Generate a new code from the Companion app and try again.",
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
          "Companion pairing mode is not active. Open the Companion app, choose Start Pairing, and enter the current code.",
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
          "Pairing could not be confirmed because the backend pairing credential was rejected. Generate a new code and retry.",
        notificationType: "error",
      };
    }

    if (lowerMessage.includes("invalid")) {
      return {
        state: "failed",
        message:
          "The pairing code was rejected. Verify the current code from the Companion app and try again.",
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
    companionLocalConnectionUnavailable,
    companionLocalHttpsStatus,
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
  const [isFirefoxBrowser, setIsFirefoxBrowser] = useState(false);
  const [firefoxPreferenceCopied, setFirefoxPreferenceCopied] = useState(false);
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

  useEffect(() => {
    if (typeof navigator !== "undefined" && /firefox/i.test(navigator.userAgent)) {
      setIsFirefoxBrowser(true);
    }
  }, []);

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
  const pairingCodeLength = pairingCode.replace(/[^A-Z0-9]/g, "").length;
  const versionMismatch =
    companionAuthenticated &&
    backendVersion &&
    companionVersion &&
    backendVersion !== companionVersion;
  const showFirefoxSetupGuidance =
    isFirefoxBrowser &&
    (pairingAttemptState === "failed" || pairingAttemptState === "unreachable");
  const canShowPairingWorkflow =
    !companionAuthenticated &&
    companionLocalHttpsStatus !== "needs-repair" &&
    companionLocalHttpsStatus !== "repairing" &&
    !versionMismatch &&
    pairingAttemptState !== "blocked";
  const showFirefoxRecoveryCard =
    showFirefoxSetupGuidance && canShowPairingWorkflow;
  const connectionStateCard = buildConnectionStateCard({
    backendVersion,
    companion,
    companionAuthenticated,
    companionLocalConnectionUnavailable,
    localHttpsStatus: companionLocalHttpsStatus,
    companionStatus,
    companionVersion,
    showFirefoxSetupGuidance,
    pairingAttemptState,
    pairingError,
  });
  const pairingCardState = buildPairingCardState({
    pairingAttemptState,
    isFirefoxBrowser,
    showFirefoxSetupGuidance,
  });

  const handleDownloadCompanion = () => {
    const platform = detectPlatform();

    if (platform === "windows" && companionReleases?.windows_url) {
      window.open(companionReleases.windows_url, "_blank");
      return;
    }

    const downloadUrl = getDownloadUrl();
    window.open(downloadUrl, "_blank");
  };

  const handleCopyFirefoxPreference = async () => {
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      setPairingError(`Copy ${FIREFOX_ENTERPRISE_ROOTS_PREF} manually.`);
      return;
    }

    try {
      await navigator.clipboard.writeText(FIREFOX_ENTERPRISE_ROOTS_PREF);
      setFirefoxPreferenceCopied(true);
      window.setTimeout(() => setFirefoxPreferenceCopied(false), 1800);
    } catch {
      setPairingError(`Copy ${FIREFOX_ENTERPRISE_ROOTS_PREF} manually.`);
    }
  };

  const handlePairCompanion = async (
    event?: FormEvent<HTMLFormElement>,
  ) => {
    event?.preventDefault();

    if (pairingCodeLength !== 8) {
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
      const failure = resolvePairingFailure(error, isFirefoxBrowser);
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

  const toolsMessage = !companion
    ? "Install or relaunch the Windows Companion on this machine before pairing here."
    : companionUpdateAvailable
      ? "A Companion update is available for this machine. Use the native flow to finish installation."
      : "Download the latest Windows installer for another machine, or use the native app to review local updates.";

  return (
    <div className="space-y-8">
      {showOverview && (
        <section className="space-y-4">
          <div>
            <h3 className="text-lg font-medium text-gray-900 dark:text-white">
              Companion Connection
            </h3>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              Confirm the current pairing state, follow the next native-owned step,
              and finish browser-side pairing only when the state below allows it.
            </p>
          </div>

          <div
            className={`rounded-2xl border px-5 py-5 ${CALLOUT_STYLES[connectionStateCard.tone]}`}
          >
            <div className="flex items-start gap-3">
              <div className="rounded-full bg-white/70 p-2 dark:bg-gray-950/40">
                {connectionStateCard.status === "Connected" ? (
                  <ShieldCheck className="h-4 w-4" />
                ) : (
                  <Link2 className="h-4 w-4" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em]">
                  Connection state
                </div>
                <h4 className="mt-2 text-xl font-semibold">
                  {connectionStateCard.status}
                </h4>
                <p className="mt-3 text-sm leading-6">
                  {connectionStateCard.message}
                </p>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-2 text-xs">
              <span className="rounded-full border border-current/20 px-3 py-1">
                Nojoin version: {backendVersion || "Unavailable"}
              </span>
              <span className="rounded-full border border-current/20 px-3 py-1">
                Companion version: {companionVersion || "Unavailable"}
              </span>
              <span className="rounded-full border border-current/20 px-3 py-1">
                Local port: {companionConfig?.local_port || 12345}
              </span>
            </div>

            <div className="mt-5 border-t border-current/15 pt-4">
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] opacity-80">
                Next step
              </div>
              <div className="mt-2 flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-3">
                <p className="text-sm font-semibold">
                  {connectionStateCard.primaryActionLabel}
                </p>
                <p className="text-sm leading-6 opacity-90">
                  {connectionStateCard.primaryActionMessage}
                </p>
              </div>
            </div>
          </div>

          {canShowPairingWorkflow && (
            <div className={`${SURFACE_CARD_STYLES} space-y-4`}>
              <div className="flex items-start gap-3">
                <div className="rounded-full bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-200">
                  <Link2 className="h-4 w-4" />
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                    Pair in browser
                  </div>
                  <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                    {pairingCardState.title}
                  </h4>
                  <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                    {pairingCardState.message}
                  </p>
                </div>
              </div>

              <form
                className="space-y-3"
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
                  {pairingCardState.helperText}
                </p>

                {pairingError && (
                  <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
                    {pairingError}
                  </div>
                )}

                {pairingNotice && (
                  <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-300">
                    {pairingNotice}
                  </div>
                )}

                <div className="flex flex-col gap-3 pt-1 sm:flex-row sm:justify-end">
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
                      pairingCodeLength !== 8
                    }
                    className="rounded-xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 dark:disabled:bg-orange-900/40"
                  >
                    {isPairingCompanion ? "Pairing..." : "Complete Pairing"}
                  </button>
                </div>
              </form>
            </div>
          )}

          {showFirefoxRecoveryCard && (
            <div className={`${SURFACE_CARD_STYLES} space-y-4`}>
              <div className="flex items-start gap-3">
                <div className="rounded-full bg-amber-100 p-2 text-amber-700 dark:bg-amber-500/10 dark:text-amber-200">
                  <ShieldCheck className="h-4 w-4" />
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-amber-700 dark:text-amber-200">
                    Firefox recovery
                  </div>
                  <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                    Complete Firefox support, then retry
                  </h4>
                  <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                    Firefox pairing did not complete in this browser. Finish the
                    Firefox support steps, restart Firefox, then retry with a fresh
                    code.
                  </p>
                </div>
              </div>

              <ol className="space-y-2 text-sm leading-6 text-gray-700 dark:text-gray-300">
                <li>1. Enable Firefox Support in the native Companion app.</li>
                <li>2. Turn on Firefox enterprise roots.</li>
                <li>3. Restart Firefox.</li>
                <li>4. Generate a fresh pairing code.</li>
              </ol>

              <p className="text-sm leading-6 text-gray-700 dark:text-gray-300">
                In Firefox, open <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-900 dark:bg-gray-900 dark:text-gray-100">about:config</span>,
                search for <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-900 dark:bg-gray-900 dark:text-gray-100">{FIREFOX_ENTERPRISE_ROOTS_PREF}</span>,
                and make sure it is set to <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-900 dark:bg-gray-900 dark:text-gray-100">true</span>
                before restarting Firefox.
              </p>

              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <button
                  type="button"
                  onClick={() => void handleCopyFirefoxPreference()}
                  className="inline-flex items-center gap-2 rounded-xl border border-amber-300 bg-white px-3 py-2 text-xs font-semibold text-amber-900 transition hover:bg-amber-50 dark:border-amber-500/30 dark:bg-gray-950 dark:text-amber-100 dark:hover:bg-gray-900"
                >
                  <Copy className="h-3.5 w-3.5" />
                  {firefoxPreferenceCopied ? "Copied" : "Copy setting name"}
                </button>
                <p className="text-sm leading-6 text-gray-600 dark:text-gray-300 sm:max-w-md sm:text-right">
                  After restarting Firefox, generate a fresh pairing code and enter it here.
                </p>
              </div>
            </div>
          )}
        </section>
      )}

      {showActions && (
        <section className="space-y-4">
          <div>
            <h3 className="text-lg font-medium text-gray-900 dark:text-white">
              Companion Tools
            </h3>
          </div>

          <div className={`${SURFACE_CARD_STYLES} space-y-4`}>
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Companion tools
              </div>
              <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                Install or update Companion
              </h4>
              <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                {toolsMessage}
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
              {companion &&
                companionUpdateAvailable &&
                companionLocalHttpsStatus !== "needs-repair" && (
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
              Manage local device selection, minimum meeting length, and
              recording-time warning preferences for the Companion app.
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
    </div>
  );
}
