import type {
  CompanionLocalHttpsStatus,
  CompanionRuntimeStatus,
} from "@/lib/serviceStatusStore";

export interface CompanionSteadyStateSnapshot {
  companion: boolean;
  companionAuthenticated: boolean;
  companionLocalConnectionUnavailable: boolean;
  companionLocalHttpsStatus: CompanionLocalHttpsStatus | null;
  companionStatus: CompanionRuntimeStatus;
  backendVersion?: string | null;
  companionVersion?: string | null;
}

export interface CompanionSteadyStateGuidance {
  key:
    | "local-browser-connection-unavailable"
    | "version-mismatch"
    | "not-paired"
    | "temporarily-disconnected"
    | "local-browser-connection-recovering"
    | "connected"
    | "companion-needs-attention";
  statusLabel: string;
  summary: string;
  nextStepLabel: string;
  nextStepMessage: string;
  tone: "success" | "info" | "warning" | "error";
}

export const hasCompanionVersionMismatch = (
  companionAuthenticated: boolean,
  backendVersion?: string | null,
  companionVersion?: string | null,
) =>
  Boolean(
    companionAuthenticated &&
      backendVersion &&
      companionVersion &&
      backendVersion !== companionVersion,
  );

export const getCompanionSteadyStateGuidance = ({
  companion,
  companionAuthenticated,
  companionLocalConnectionUnavailable,
  companionLocalHttpsStatus,
  companionStatus,
  backendVersion,
  companionVersion,
}: CompanionSteadyStateSnapshot): CompanionSteadyStateGuidance => {
  if (companionLocalHttpsStatus === "needs-repair") {
    return {
      key: "local-browser-connection-unavailable",
      statusLabel: "Local browser connection unavailable",
      summary:
        "Local browser controls are unavailable until the Companion restores its secure local connection.",
      nextStepLabel: "Relaunch Companion",
      nextStepMessage:
        "Quit and relaunch the Companion app on this device, then retry browser-side local controls.",
      tone: "warning",
    };
  }

  if (
    hasCompanionVersionMismatch(
      companionAuthenticated,
      backendVersion,
      companionVersion,
    )
  ) {
    return {
      key: "version-mismatch",
      statusLabel: "Version mismatch",
      summary:
        "Nojoin and the Companion must be on compatible versions before local control will work again.",
      nextStepLabel: "Open Settings",
      nextStepMessage:
        "Open Companion support, then align versions before retrying local controls.",
      tone: "error",
    };
  }

  if (!companionAuthenticated) {
    return {
      key: "not-paired",
      statusLabel: "Not paired",
      summary: "This deployment is not paired to a Companion yet.",
      nextStepLabel: "Pair This Device",
      nextStepMessage:
        "Use the Nojoin settings page to start pairing, then approve the request in the Companion app.",
      tone: "info",
    };
  }

  if (!companion) {
    return {
      key: "temporarily-disconnected",
      statusLabel: "Temporarily disconnected",
      summary: companionLocalConnectionUnavailable
        ? "The pairing is still valid, but this browser cannot reach the local Companion right now."
        : "The pairing is still valid and should recover automatically when the Companion reconnects.",
      nextStepLabel: "Open Settings",
      nextStepMessage:
        "Wait for reconnect first. Open Companion support if the connection does not return.",
      tone: "info",
    };
  }

  if (companionLocalHttpsStatus === "repairing") {
    return {
      key: "local-browser-connection-recovering",
      statusLabel: "Local browser connection recovering",
      summary:
        "The Companion is restoring its local browser connection. Browser controls will refresh automatically when recovery finishes.",
      nextStepLabel: "Wait For Recovery",
      nextStepMessage:
        "Wait for reconnect first. If this state lingers, quit and relaunch the Companion app.",
      tone: "info",
    };
  }

  if (companionStatus === "error") {
    return {
      key: "companion-needs-attention",
      statusLabel: "Connected",
      summary:
        "The Companion is still paired to this deployment, but it reported a local problem.",
      nextStepLabel: "Open Settings",
      nextStepMessage:
        "Open Companion support and review the current local status before retrying.",
      tone: "warning",
    };
  }

  return {
    key: "connected",
    statusLabel: "Connected",
    summary:
      companionStatus === "uploading"
        ? "The Companion is still uploading work from the previous meeting."
        : companionStatus === "backend-offline"
          ? "The Companion is paired, but Nojoin is offline right now."
          : "The Companion is paired and ready for local recording controls.",
    nextStepLabel: "Open Settings",
    nextStepMessage:
      "Open Companion support if you need setup or troubleshooting guidance.",
    tone: "success",
  };
};