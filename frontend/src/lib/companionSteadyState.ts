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
    | "browser-repair-required"
    | "version-mismatch"
    | "not-paired"
    | "temporarily-disconnected"
    | "browser-repair-in-progress"
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
      key: "browser-repair-required",
      statusLabel: "Browser repair required",
      summary:
        "Local browser controls are blocked until the Companion repair flow finishes.",
      nextStepLabel: "Open Settings to Repair",
      nextStepMessage:
        "Open Companion support, then follow Open Settings to Repair in the Companion app.",
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
      nextStepLabel: "Start Pairing",
      nextStepMessage:
        "Open Companion support, then start pairing in the Companion app.",
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
      key: "browser-repair-in-progress",
      statusLabel: "Browser repair in progress",
      summary:
        "The Companion is repairing its local browser connection. Browser controls will refresh automatically when the repair finishes.",
      nextStepLabel: "Open Settings",
      nextStepMessage:
        "Wait for reconnect first. Open Companion support if this takes longer than expected.",
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