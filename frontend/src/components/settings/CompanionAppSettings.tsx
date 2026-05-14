"use client";

import { useEffect, useRef, useState } from "react";
import { Link2 } from "lucide-react";

import { CompanionDevices } from "@/types";
import {
  type CompanionLocalHttpsStatus,
  type CompanionRuntimeStatus,
  useServiceStatusStore,
} from "@/lib/serviceStatusStore";
import {
  cancelCompanionPairingRequest,
  CompanionPairingRequestError,
  type CompanionPairingRequestCreateResponse,
  type CompanionPairingRequestState,
  createCompanionPairingRequest,
  getCompanionPairingRequestStatus,
} from "@/lib/companionPairingApi";
import { useNotificationStore } from "@/lib/notificationStore";
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
  | "launching"
  | "cancelled"
  | CompanionPairingRequestState;

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
  primaryActionLabel: string;
}

const STATUS_CHIP_STYLES: Record<CalloutTone, string> = {
  success:
    "border-green-200 bg-green-50 text-green-800 dark:border-green-500/30 dark:bg-green-500/10 dark:text-green-200",
  info:
    "border-blue-200 bg-blue-50 text-blue-800 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200",
  warning:
    "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200",
  error:
    "border-red-200 bg-red-50 text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200",
};

const NOTICE_STYLES: Record<CalloutTone, string> = {
  success:
    "border-green-200/80 bg-green-50/80 text-green-800 dark:border-green-500/20 dark:bg-green-500/10 dark:text-green-200",
  info:
    "border-blue-200/80 bg-blue-50/80 text-blue-800 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-200",
  warning:
    "border-amber-200/80 bg-amber-50/80 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200",
  error:
    "border-red-200/80 bg-red-50/80 text-red-800 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200",
};

const SECTION_CARD_STYLES =
  "rounded-[28px] border border-gray-200/80 bg-white/95 p-6 shadow-sm shadow-gray-200/60 backdrop-blur dark:border-gray-800 dark:bg-gray-950/90 dark:shadow-none";

const PANEL_STYLES =
  "rounded-2xl border border-gray-200/80 bg-gray-50/85 p-4 dark:border-gray-800 dark:bg-gray-900/70";

const META_CARD_STYLES =
  "rounded-2xl border border-gray-200/80 bg-white/90 p-4 dark:border-gray-800 dark:bg-gray-950/80";

const buildConnectionStateCard = ({
  backendVersion,
  companion,
  companionAuthenticated,
  companionLocalConnectionUnavailable,
  localHttpsStatus,
  companionStatus,
  companionVersion,
  pairingAttemptState,
  pairingMessage,
}: {
  backendVersion: string | null;
  companion: boolean;
  companionAuthenticated: boolean;
  companionLocalConnectionUnavailable: boolean;
  localHttpsStatus: CompanionLocalHttpsStatus | null;
  companionStatus: CompanionRuntimeStatus;
  companionVersion: string | null;
  pairingAttemptState: PairingAttemptState;
  pairingMessage: string | null;
}): StatusCardState => {
  const versionMismatch =
    companionAuthenticated &&
    backendVersion &&
    companionVersion &&
    backendVersion !== companionVersion;

  if (localHttpsStatus === "needs-repair") {
    return {
      status: "Local browser connection unavailable",
      message:
        "Local browser controls are unavailable until the Companion restores its secure local connection.",
      tone: "warning",
      primaryActionLabel: "Relaunch Companion",
      primaryActionMessage:
        "Quit and relaunch the Companion app on this device, then retry pairing or browser-side local controls here.",
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
        "Use the Companion app to review update status. If the update clears trust state, pair this device again after versions align.",
    };
  }

  if (pairingAttemptState === "opened") {
    return {
      status: "Approval pending",
      message:
        "The Companion received the pairing request and is waiting for an OS-native accept or decline decision.",
      tone: "info",
      primaryActionLabel: "Approve Native Prompt",
      primaryActionMessage:
        "Use the operating system prompt opened by Nojoin Companion to approve or decline this browser request.",
    };
  }

  if (pairingAttemptState === "completing") {
    return {
      status: "Completing pairing",
      message:
        "The Companion approved the request and is finishing secure backend registration now.",
      tone: "info",
      primaryActionLabel: "Wait For Completion",
      primaryActionMessage:
        "This page will refresh automatically when secure pairing completes.",
    };
  }

  if (pairingAttemptState === "declined") {
    return {
      status: "Pairing declined",
      message: pairingMessage || "The local Companion declined the browser pairing request.",
      tone: "warning",
      primaryActionLabel: "Pair This Device",
      primaryActionMessage:
        "Start a new request when you are ready to approve it in the Companion app.",
    };
  }

  if (pairingAttemptState === "expired") {
    return {
      status: "Pairing expired",
      message: pairingMessage || "The pairing request expired before it was approved.",
      tone: "warning",
      primaryActionLabel: "Pair This Device",
      primaryActionMessage:
        "Start a new request and approve it from the Companion app before it times out.",
    };
  }

  if (pairingAttemptState === "failed") {
    return {
      status: "Pairing failed",
      message: pairingMessage || "The browser could not complete the pairing request.",
      tone: "error",
      primaryActionLabel: "Retry Pairing",
      primaryActionMessage:
        "Start a fresh request after you review the error in the browser or Companion app.",
    };
  }

  if (pairingAttemptState === "cancelled") {
    return {
      status: "Pairing cancelled",
      message: pairingMessage || "The pending pairing request was cancelled.",
      tone: "info",
      primaryActionLabel: "Pair This Device",
      primaryActionMessage:
        "Start a new request when you want to pair this Companion again.",
    };
  }

  if (!companionAuthenticated) {
    return {
      status: "Not paired",
      message: companionLocalConnectionUnavailable
        ? "This deployment is not paired to a Companion yet, and the browser cannot reach the local app right now."
        : "This deployment is not paired to a Companion yet.",
      tone: "info",
      primaryActionLabel: "Pair This Device",
      primaryActionMessage:
        "Start pairing here, then approve the request in the local Companion app when it opens.",
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
      status: "Local browser connection recovering",
      message:
        "The Companion is restoring its local browser connection. This page will refresh automatically when recovery completes.",
      tone: "info",
      primaryActionLabel: "Wait For Recovery",
      primaryActionMessage:
        "Wait for this state to clear first. If it lingers, quit and relaunch the Companion app.",
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
  companionLocalConnectionUnavailable,
}: {
  pairingAttemptState: PairingAttemptState;
  companionLocalConnectionUnavailable: boolean;
}): PairingCardState => {
  if (pairingAttemptState === "launching") {
    return {
      title: "Launching Companion",
      message:
        "The browser is opening the local Companion app with a signed pairing request.",
      helperText:
        "If nothing appears, or Windows says no app is associated with nojoin://, relaunch Nojoin Companion and start a fresh request here.",
      primaryActionLabel: "Launching...",
    };
  }

  if (pairingAttemptState === "pending") {
    return {
      title: "Waiting for Companion",
      message:
        "Approve the request in the Companion app after it opens on this device.",
      helperText:
        "Keep this page open while the request moves from pending to approval. If the Companion never opens, relaunch it and retry the request.",
      primaryActionLabel: "Waiting For Approval",
    };
  }

  if (pairingAttemptState === "opened") {
    return {
      title: "Approval required",
      message:
        "The Companion launched an OS-native prompt to accept or decline this browser pairing request.",
      helperText:
        "Approving in that prompt completes secure backend registration for this browser session.",
      primaryActionLabel: "Awaiting Approval",
    };
  }

  if (pairingAttemptState === "completing") {
    return {
      title: "Completing pairing",
      message:
        "The Companion approved the request and is exchanging final credentials with the backend.",
      helperText:
        "This page will mark the device as paired as soon as the backend confirms completion.",
      primaryActionLabel: "Finalizing...",
    };
  }

  if (pairingAttemptState === "declined") {
    return {
      title: "Pairing declined",
      message:
        "The local Companion declined this browser request.",
      helperText:
        "Start a new request when you want to try again.",
      primaryActionLabel: "Start New Request",
    };
  }

  if (pairingAttemptState === "expired") {
    return {
      title: "Request expired",
      message:
        "The signed request expired before it was approved in the Companion app.",
      helperText:
        "Start a new request and approve it from the native prompt before it times out.",
      primaryActionLabel: "Start New Request",
    };
  }

  if (pairingAttemptState === "failed") {
    return {
      title: "Retry pairing",
      message:
        "The browser or Companion could not complete the request.",
      helperText:
        "Review the error below, then start a fresh request.",
      primaryActionLabel: "Retry Pairing",
    };
  }

  if (pairingAttemptState === "cancelled") {
    return {
      title: "Request cancelled",
      message:
        "The pending request was cancelled before the Companion approved it.",
      helperText:
        "You can start another request whenever you are ready.",
      primaryActionLabel: "Start New Request",
    };
  }

  return {
    title: "Pair this device",
    message: companionLocalConnectionUnavailable
      ? "The browser cannot currently reach the local Companion status endpoint, but pairing still starts from this page and completes in the native app."
      : "Start secure pairing from this browser, then approve the request in the local Companion app.",
    helperText:
      "No pairing code fallback exists. Each request is signed by the backend and must be explicitly approved on this device.",
    primaryActionLabel: "Pair This Device",
  };
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
    checkCompanion,
    markCompanionPairingCompleted,
  } = useServiceStatusStore();
  const { addNotification } = useNotificationStore();
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [pairingNotice, setPairingNotice] = useState<string | null>(null);
  const [pairingRequest, setPairingRequest] =
    useState<CompanionPairingRequestCreateResponse | null>(null);
  const [pairingAttemptState, setPairingAttemptState] =
    useState<PairingAttemptState>("idle");
  const [isPairingCompanion, setIsPairingCompanion] = useState(false);
  const [isCancellingPendingPairing, setIsCancellingPendingPairing] =
    useState(false);
  const pairingRequestInFlightRef = useRef(false);
  const pairingLaunchFrameRef = useRef<HTMLIFrameElement | null>(null);
  const pairingLaunchWindowRef = useRef<Window | null>(null);
  const pairingLaunchCleanupTimerRef = useRef<number | null>(null);
  const handledTerminalRequestIdRef = useRef<string | null>(null);
  const refreshCompanionConfigRef = useRef(onRefreshCompanionConfig);

  const closePairingLaunchWindow = () => {
    const launchWindow = pairingLaunchWindowRef.current;
    if (launchWindow && !launchWindow.closed) {
      try {
        launchWindow.close();
      } catch {
        // Ignore cross-browser close failures for external protocol handoff windows.
      }
    }
    pairingLaunchWindowRef.current = null;
  };

  const clearPairingLaunchArtifacts = () => {
    if (pairingLaunchCleanupTimerRef.current !== null) {
      window.clearTimeout(pairingLaunchCleanupTimerRef.current);
      pairingLaunchCleanupTimerRef.current = null;
    }

    pairingLaunchFrameRef.current?.remove();
    pairingLaunchFrameRef.current = null;
    closePairingLaunchWindow();
  };

  const primePairingLaunchWindow = () => {
    closePairingLaunchWindow();

    // Open a script-owned window during the original click so the later
    // protocol navigation still counts as a user-initiated launch.
    const launchWindow = window.open(
      "",
      "nojoin-pairing-launch",
      "popup=yes,width=380,height=210,left=120,top=120",
    );

    if (!launchWindow) {
      return null;
    }

    pairingLaunchWindowRef.current = launchWindow;

    try {
      launchWindow.document.title = "Opening Nojoin Companion";
      launchWindow.document.body.innerHTML =
        '<main style="min-height: 100vh; margin: 0; padding: 24px; background: #fff7ed; color: #111827; font-family: ui-sans-serif, system-ui, sans-serif; line-height: 1.5;"><h1 style="margin: 0 0 12px; font-size: 18px;">Opening Nojoin Companion...</h1><p style="margin: 0; font-size: 14px; color: #7c2d12;">If the native prompt does not appear, return to Nojoin and use Reopen Native Prompt.</p></main>';
      launchWindow.document.body.style.margin = '0';
      launchWindow.document.body.style.background = '#fff7ed';
      launchWindow.document.body.style.color = '#111827';
    } catch {
      // The window is still usable for protocol navigation even if we cannot paint content.
    }

    return launchWindow;
  };

  useEffect(() => {
    refreshCompanionConfigRef.current = onRefreshCompanionConfig;
  }, [onRefreshCompanionConfig]);

  useEffect(() => {
    return () => {
      if (pairingLaunchCleanupTimerRef.current !== null) {
        window.clearTimeout(pairingLaunchCleanupTimerRef.current);
        pairingLaunchCleanupTimerRef.current = null;
      }

      pairingLaunchFrameRef.current?.remove();
      pairingLaunchFrameRef.current = null;

      const launchWindow = pairingLaunchWindowRef.current;
      if (launchWindow && !launchWindow.closed) {
        try {
          launchWindow.close();
        } catch {
          // Ignore cross-browser close failures during component teardown.
        }
      }
      pairingLaunchWindowRef.current = null;
    };
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

    setPairingRequest(null);
    handledTerminalRequestIdRef.current = null;
    setPairingAttemptState("idle");
    setPairingError(null);
    setPairingNotice(null);
    void onRefreshCompanionConfig?.();
  }, [companionAuthenticated, onRefreshCompanionConfig]);

  useEffect(() => {
    if (!pairingRequest) {
      return;
    }

    let isCancelled = false;

    const pollPairingRequest = async () => {
      try {
        const status = await getCompanionPairingRequestStatus(
          pairingRequest.request_id,
        );

        if (isCancelled) {
          return;
        }

        setPairingAttemptState(status.status);

        if (status.status === "pending") {
          setPairingNotice(
            status.detail ||
              "Waiting for Nojoin Companion to open and acknowledge the pairing request.",
          );
          return;
        }

        if (status.status === "opened") {
          setPairingNotice(
            status.detail ||
              "Approve or decline the pairing request in the local Companion app.",
          );
          return;
        }

        if (status.status === "completing") {
          setPairingNotice(
            status.detail ||
              "Companion approved the request and is finishing secure backend registration.",
          );
          return;
        }

        if (handledTerminalRequestIdRef.current === pairingRequest.request_id) {
          return;
        }

        handledTerminalRequestIdRef.current = pairingRequest.request_id;
        setPairingRequest(null);

        if (status.status === "completed") {
          setPairingError(null);
          setPairingNotice(
            status.detail ||
              "Companion paired successfully. Local recording controls are ready.",
          );
          await markCompanionPairingCompleted();

          for (let attempt = 0; attempt < 6; attempt += 1) {
            const latestStoreState = useServiceStatusStore.getState();
            if (!latestStoreState.companionAuthenticated) {
              await latestStoreState.checkCompanion();
            }

            if (useServiceStatusStore.getState().companionAuthenticated) {
              await refreshCompanionConfigRef.current?.();
              break;
            }

            await new Promise<void>((resolve) => {
              window.setTimeout(resolve, 250);
            });
          }

          setPairingAttemptState("idle");
          addNotification({
            type: "success",
            message:
              "Companion paired successfully. Local recording controls are ready on this Nojoin deployment.",
          });
          return;
        }

        const terminalMessage =
          status.detail ||
          (status.status === "declined"
            ? "The local Companion declined the pairing request."
            : status.status === "cancelled"
              ? "The pending pairing request was cancelled."
              : status.status === "expired"
                ? "The pairing request expired before it was approved."
                : "The browser could not complete the pairing request.");

        setPairingNotice(null);
        setPairingError(status.status === "cancelled" ? null : terminalMessage);
        if (status.status === "cancelled") {
          setPairingNotice(terminalMessage);
        }
        addNotification({
          type:
            status.status === "declined" || status.status === "expired"
              ? "warning"
              : status.status === "cancelled"
                ? "info"
                : "error",
          message: terminalMessage,
        });
      } catch (error) {
        if (isCancelled) {
          return;
        }

        const message =
          error instanceof Error
            ? error.message
            : "Failed to load the latest pairing request status.";
        setPairingRequest(null);
        setPairingAttemptState("failed");
        setPairingNotice(null);
        setPairingError(message);
        addNotification({
          type: "error",
          message,
        });
      }
    };

    void pollPairingRequest();
    const intervalId = window.setInterval(() => {
      void pollPairingRequest();
    }, 1500);

    return () => {
      isCancelled = true;
      window.clearInterval(intervalId);
    };
  }, [addNotification, markCompanionPairingCompleted, pairingRequest]);

  const showOverview = !searchQuery || fuzzyMatch(searchQuery, COMPANION_KEYWORDS);
  const showAudioSections = !searchQuery || fuzzyMatch(searchQuery, AUDIO_KEYWORDS);

  if (searchQuery && !showOverview && !showAudioSections) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  const versionMismatch =
    companionAuthenticated &&
    backendVersion &&
    companionVersion &&
    backendVersion !== companionVersion;
  const canShowPairingWorkflow =
    !companionAuthenticated &&
    companionLocalHttpsStatus !== "needs-repair" &&
    companionLocalHttpsStatus !== "repairing" &&
    !versionMismatch;
  const connectionStateCard = buildConnectionStateCard({
    backendVersion,
    companion,
    companionAuthenticated,
    companionLocalConnectionUnavailable,
    localHttpsStatus: companionLocalHttpsStatus,
    companionStatus,
    companionVersion,
    pairingAttemptState,
    pairingMessage: pairingError || pairingNotice,
  });
  const pairingCardState = buildPairingCardState({
    pairingAttemptState,
    companionLocalConnectionUnavailable,
  });
  const hasActivePendingRequest =
    pairingRequest !== null &&
    pairingAttemptState !== "cancelled" &&
    pairingAttemptState !== "declined" &&
    pairingAttemptState !== "expired" &&
    pairingAttemptState !== "failed";
  const pairingFeedbackMessage = pairingError || pairingNotice;
  const pairingFeedbackTone: CalloutTone = pairingError
    ? "error"
    : companionAuthenticated && pairingNotice
      ? "success"
      : pairingAttemptState === "declined" || pairingAttemptState === "expired"
        ? "warning"
        : "info";
  const localControlsLabel =
    companionLocalHttpsStatus === "needs-repair"
      ? "Unavailable"
      : companionLocalHttpsStatus === "repairing"
        ? "Recovering"
        : companionLocalHttpsStatus === "ready"
          ? "Ready"
          : companionLocalHttpsStatus === "disabled"
            ? "Disabled"
            : "Unavailable";

  const openPairingLaunchUrl = (
    request: CompanionPairingRequestCreateResponse,
    launchWindow?: Window | null,
  ) => {
    if (pairingLaunchCleanupTimerRef.current !== null) {
      window.clearTimeout(pairingLaunchCleanupTimerRef.current);
      pairingLaunchCleanupTimerRef.current = null;
    }

    pairingLaunchFrameRef.current?.remove();
    pairingLaunchFrameRef.current = null;

    const targetWindow = launchWindow ?? pairingLaunchWindowRef.current;
    if (targetWindow && !targetWindow.closed) {
      pairingLaunchWindowRef.current = targetWindow;
      try {
        targetWindow.location.href = request.launch_url;
      } catch {
        closePairingLaunchWindow();
      }

      pairingLaunchCleanupTimerRef.current = window.setTimeout(() => {
        clearPairingLaunchArtifacts();
      }, 4000);
      return;
    }

    closePairingLaunchWindow();

    const launchFrame = document.createElement("iframe");
    launchFrame.setAttribute("aria-hidden", "true");
    launchFrame.tabIndex = -1;
    launchFrame.style.position = "fixed";
    launchFrame.style.width = "1px";
    launchFrame.style.height = "1px";
    launchFrame.style.opacity = "0";
    launchFrame.style.pointerEvents = "none";
    launchFrame.style.border = "0";
    launchFrame.style.left = "-9999px";
    launchFrame.style.top = "-9999px";
    document.body.appendChild(launchFrame);

    pairingLaunchFrameRef.current = launchFrame;
    launchFrame.src = request.launch_url;
    pairingLaunchCleanupTimerRef.current = window.setTimeout(() => {
      clearPairingLaunchArtifacts();
    }, 4000);
  };

  const handlePairCompanion = async () => {
    if (pairingRequestInFlightRef.current) {
      return;
    }

    const primedLaunchWindow = primePairingLaunchWindow();

    pairingRequestInFlightRef.current = true;
    handledTerminalRequestIdRef.current = null;
    setIsPairingCompanion(true);
    setPairingAttemptState("launching");
    setPairingError(null);
    setPairingNotice("Creating a signed pairing request and opening Nojoin Companion.");
    try {
      const request = await createCompanionPairingRequest();
      setPairingRequest(request);
      setPairingAttemptState(request.status);
      setPairingNotice(
        request.replacement
          ? "This request will replace the current Companion pairing after you approve it in the native app."
          : "Approve the pairing request in Nojoin Companion on this device.",
      );
      openPairingLaunchUrl(request, primedLaunchWindow);
    } catch (error) {
      if (primedLaunchWindow && !primedLaunchWindow.closed) {
        try {
          primedLaunchWindow.close();
        } catch {
          // Ignore browser-specific close failures for a primed launch window.
        }
      }
      pairingLaunchWindowRef.current = null;
      const message =
        error instanceof CompanionPairingRequestError || error instanceof Error
          ? error.message
          : "Failed to create a new Companion pairing request.";
      setPairingRequest(null);
      setPairingAttemptState("failed");
      setPairingNotice(null);
      setPairingError(message);
      addNotification({
        type:
          error instanceof CompanionPairingRequestError && error.status === 409
            ? "warning"
            : "error",
        message,
      });
    } finally {
      pairingRequestInFlightRef.current = false;
      setIsPairingCompanion(false);
    }
  };

  const handleCancelPendingPairing = async () => {
    if (!pairingRequest) {
      return;
    }

    setIsCancellingPendingPairing(true);
    setPairingNotice(null);

    try {
      await cancelCompanionPairingRequest(pairingRequest.request_id);
      handledTerminalRequestIdRef.current = pairingRequest.request_id;
      setPairingRequest(null);
      setPairingAttemptState("cancelled");
      setPairingError(null);
      setPairingNotice(
        "Pending pairing request cancelled. Start a new request whenever you are ready.",
      );
      addNotification({
        type: "info",
        message:
          "Pending pairing request cancelled. Start a new request whenever you are ready.",
      });
    } catch (error: unknown) {
      const message =
        error instanceof CompanionPairingRequestError || error instanceof Error
          ? error.message
          : "Failed to cancel the pending pairing request.";
      setPairingError(message);
      addNotification({
        type: "error",
        message,
      });
    } finally {
      setIsCancellingPendingPairing(false);
    }
  };

  return (
    <div className="space-y-6">
      {showOverview && (
        <section className={`${SECTION_CARD_STYLES} space-y-5`}>
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="max-w-2xl">
              <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-gray-500 dark:text-gray-400">
                Companion app
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <h3 className="text-xl font-semibold text-gray-900 dark:text-white">
                  Connection and pairing
                </h3>
                <span
                  className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${STATUS_CHIP_STYLES[connectionStateCard.tone]}`}
                >
                  {connectionStateCard.status}
                </span>
              </div>
              <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
                {connectionStateCard.message}
              </p>
            </div>

            <div className={`${PANEL_STYLES} xl:max-w-sm`}>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Next step
              </div>
              <p className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                {connectionStateCard.primaryActionLabel}
              </p>
              <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                {connectionStateCard.primaryActionMessage}
              </p>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <div className={META_CARD_STYLES}>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Nojoin deployment
              </div>
              <div className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                {backendVersion || "Unavailable"}
              </div>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                Backend version reported by this browser session.
              </p>
            </div>

            <div className={META_CARD_STYLES}>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Local Companion
              </div>
              <div className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                {companionVersion || "Unavailable"}
              </div>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                Runtime version currently visible to the browser on this machine.
              </p>
            </div>

            <div className={META_CARD_STYLES}>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Browser controls
              </div>
              <div className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                {localControlsLabel}
              </div>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                Local port {companionConfig?.local_port || 12345}.
              </p>
            </div>
          </div>

          {canShowPairingWorkflow && (
            <div className={`${PANEL_STYLES} space-y-4`}>
              <div className="flex items-start gap-3">
                <div className="rounded-full bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-200">
                  <Link2 className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                    Pair from this browser
                  </div>
                  <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                    {pairingCardState.title}
                  </h4>
                  <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                    {pairingCardState.message}
                  </p>
                </div>
              </div>

              <p className="text-sm leading-6 text-gray-500 dark:text-gray-400">
                {pairingCardState.helperText}
              </p>

              {pairingRequest && (
                <div className={`${META_CARD_STYLES} space-y-3`}>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                    Pending request
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs text-gray-600 dark:text-gray-300">
                    <span className="rounded-full border border-gray-200 px-3 py-1 dark:border-gray-700">
                      Request {pairingRequest.request_id}
                    </span>
                    <span className="rounded-full border border-gray-200 px-3 py-1 dark:border-gray-700">
                      Expires {new Date(pairingRequest.expires_at).toLocaleTimeString()}
                    </span>
                    <span className="rounded-full border border-gray-200 px-3 py-1 dark:border-gray-700">
                      Native prompt required
                    </span>
                  </div>
                </div>
              )}

              {pairingFeedbackMessage && (
                <div
                  className={`rounded-2xl border px-4 py-3 text-sm ${NOTICE_STYLES[pairingFeedbackTone]}`}
                >
                  {pairingFeedbackMessage}
                </div>
              )}

              <div className="flex flex-col gap-3 pt-1 sm:flex-row sm:flex-wrap sm:justify-end">
                {hasActivePendingRequest && pairingRequest && (
                  <>
                    <button
                      type="button"
                      onClick={() =>
                        openPairingLaunchUrl(
                          pairingRequest,
                          primePairingLaunchWindow(),
                        )
                      }
                      disabled={isPairingCompanion || isCancellingPendingPairing}
                      className="rounded-xl border border-gray-300 px-4 py-3 text-sm font-semibold text-gray-700 transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-400 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-950 dark:disabled:border-gray-800 dark:disabled:text-gray-500"
                    >
                      Reopen Native Prompt
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleCancelPendingPairing()}
                      disabled={isPairingCompanion || isCancellingPendingPairing}
                      className="rounded-xl border border-orange-300 px-4 py-3 text-sm font-semibold text-orange-700 transition hover:border-orange-400 hover:bg-orange-50 disabled:cursor-not-allowed disabled:border-orange-200 disabled:text-orange-300 dark:border-orange-500/30 dark:text-orange-300 dark:hover:bg-orange-500/10 dark:disabled:border-orange-500/20 dark:disabled:text-orange-500/50"
                    >
                      {isCancellingPendingPairing
                        ? "Cancelling Request..."
                        : "Cancel Request"}
                    </button>
                  </>
                )}

                <button
                  type="button"
                  onClick={() => void handlePairCompanion()}
                  disabled={isPairingCompanion || isCancellingPendingPairing || hasActivePendingRequest}
                  className="rounded-xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 dark:disabled:bg-orange-900/40"
                >
                  {isPairingCompanion
                    ? "Starting Pairing..."
                    : pairingCardState.primaryActionLabel}
                </button>
              </div>
            </div>
          )}

          {!canShowPairingWorkflow && pairingFeedbackMessage && (
            <div
              className={`rounded-2xl border px-4 py-3 text-sm ${NOTICE_STYLES[pairingFeedbackTone]}`}
            >
              {pairingFeedbackMessage}
            </div>
          )}
        </section>
      )}

      {showAudioSections && (
        <section className={`${SECTION_CARD_STYLES} space-y-5`}>
          <div className="max-w-2xl">
            <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-gray-500 dark:text-gray-400">
              Recording preferences
            </div>
            <h3 className="mt-3 text-xl font-semibold text-gray-900 dark:text-white">
              Devices and alerts
            </h3>
            <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
              Manage local device selection, minimum meeting length, and quiet-audio warning behavior for the Companion app.
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
