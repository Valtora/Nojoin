"use client";

import { useState } from "react";
import { Mic } from "lucide-react";
import { useRouter } from "next/navigation";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import { getCompanionSteadyStateGuidance } from "@/lib/companionSteadyState";
import {
  COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE,
  companionLocalFetch,
  isCompanionLocalConnectionError,
  readCompanionLocalError,
} from "@/lib/companionLocalApi";

import LiveMeetingControls from "./LiveMeetingControls";

interface MeetingControlsProps {
  onMeetingEnd?: () => void;
  variant?: "sidebar" | "dashboard";
}

type ButtonMode = "start" | "open-settings" | "wait";

interface MeetingSurfaceState {
  buttonLabel: string;
  buttonMode: ButtonMode;
  buttonDisabled: boolean;
  buttonTooltip: string;
}

export default function MeetingControls({
  onMeetingEnd,
  variant = "sidebar",
}: MeetingControlsProps) {
  const {
    backend,
    backendVersion,
    companion,
    companionAuthenticated,
    companionLocalConnectionUnavailable,
    companionLocalHttpsStatus,
    companionStatus,
    companionVersion,
    checkCompanion,
    enableCompanionMonitoring,
  } = useServiceStatusStore();

  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const localHttpsNeedsRepair = companionLocalHttpsStatus === "needs-repair";
  const hasLiveRecording =
    companion &&
    (companionStatus === "recording" || companionStatus === "paused");
  const isCompanionUploading = companion && companionStatus === "uploading";
  const companionGuidance = getCompanionSteadyStateGuidance({
    companion,
    companionAuthenticated,
    companionLocalConnectionUnavailable,
    companionLocalHttpsStatus,
    companionStatus,
    backendVersion,
    companionVersion,
  });

  const meetingSurfaceState: MeetingSurfaceState = !backend
    ? {
        buttonLabel: "Nojoin unavailable",
        buttonMode: "wait",
        buttonDisabled: true,
        buttonTooltip:
          "The Nojoin backend is offline. Wait for it to reconnect before starting a meeting.",
      }
    : companionGuidance.key === "local-browser-connection-unavailable" ||
        companionGuidance.key === "version-mismatch" ||
        companionGuidance.key === "not-paired" ||
        companionGuidance.key === "temporarily-disconnected" ||
      companionGuidance.key === "local-browser-connection-recovering" ||
        companionGuidance.key === "companion-needs-attention"
      ? {
          buttonLabel: "Open Companion Settings",
          buttonMode: "open-settings",
          buttonDisabled: false,
          buttonTooltip: `${companionGuidance.statusLabel}. ${companionGuidance.summary}`,
        }
      : isCompanionUploading
        ? {
            buttonLabel: "Finishing upload...",
            buttonMode: "wait",
            buttonDisabled: true,
            buttonTooltip:
              "The Companion is still uploading the previous meeting. Wait for it to finish.",
          }
        : companionStatus === "backend-offline"
          ? {
              buttonLabel: "Nojoin unavailable",
              buttonMode: "wait",
              buttonDisabled: true,
              buttonTooltip:
                "The Companion is paired but the Nojoin backend is offline.",
            }
          : {
              buttonLabel: "Start Meeting",
              buttonMode: "start",
              buttonDisabled: false,
              buttonTooltip: "Start a new meeting recording.",
            };

  const sendStart = async () => {
    setError(null);
    if (localHttpsNeedsRepair) {
      setError(
        "Browser repair required. Open Companion Settings, then follow the repair steps in the Companion app.",
      );
      return null;
    }

    try {
      const res = await companionLocalFetch(
        "/start",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: "" }),
        },
        "recording:start",
      );

      if (!res.ok) {
        const errorMessage = await readCompanionLocalError(
          res,
          `Companion App error: ${res.status}`,
        );
        setError(errorMessage || "Failed to reach Backend API from Companion App.");
        return null;
      }

      enableCompanionMonitoring();
      setTimeout(() => checkCompanion(), 500);
      return await res.json();
    } catch (err: unknown) {
      if (isCompanionLocalConnectionError(err)) {
        setError(COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE);
      } else if (err instanceof Error && err.message) {
        setError(err.message);
      } else {
        setError("Failed to connect to Companion App.");
      }
      console.error(err);
      return null;
    }
  };

  const handleStart = async () => {
    const response = await sendStart();
    if (response && response.id) {
      router.push(`/recordings/${response.id}`);
      if (onMeetingEnd) onMeetingEnd();
    }
  };

  const handlePrimaryAction = () => {
    setError(null);
    if (meetingSurfaceState.buttonMode === "open-settings") {
      router.push("/settings?tab=companion");
      return;
    }
    if (meetingSurfaceState.buttonMode === "start") {
      void handleStart();
    }
  };

  if (variant === "dashboard") {
    return (
      <div className="rounded-[2rem] border border-orange-100 bg-white p-6 shadow-xl shadow-orange-900/10 backdrop-blur dark:border-gray-700/70 dark:bg-gray-900/85 dark:shadow-black/30">
        <div className="flex flex-col gap-5">
          <div className="mt-2 flex items-start gap-3">
            <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
              <Mic className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-2xl font-semibold text-gray-950 dark:text-white">
                Meet Now
              </h2>
            </div>
          </div>

          {error && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
              {error}
            </div>
          )}

          {!hasLiveRecording ? (
            <button
              type="button"
              onClick={handlePrimaryAction}
              disabled={meetingSurfaceState.buttonDisabled}
              title={meetingSurfaceState.buttonTooltip}
              aria-label={meetingSurfaceState.buttonLabel}
              className="flex items-center justify-center gap-2 rounded-2xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 dark:disabled:bg-orange-900/40"
            >
              <Mic className="h-4 w-4" />
              {meetingSurfaceState.buttonLabel}
            </button>
          ) : (
            <LiveMeetingControls size="full" onMeetingEnd={onMeetingEnd} />
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="border-b border-orange-100/80 bg-transparent p-4 dark:border-gray-800/80">
      <div className="w-full">
        {error && <div className="mb-2 text-xs text-red-500">{error}</div>}

        {!hasLiveRecording ? (
          <button
            type="button"
            onClick={handlePrimaryAction}
            disabled={meetingSurfaceState.buttonDisabled}
            title={meetingSurfaceState.buttonTooltip}
            aria-label={meetingSurfaceState.buttonLabel}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-orange-600 px-4 py-2 font-medium text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 dark:disabled:bg-orange-900/40"
          >
            <Mic className="h-4 w-4" />
            {meetingSurfaceState.buttonLabel}
          </button>
        ) : (
          <LiveMeetingControls size="compact" onMeetingEnd={onMeetingEnd} />
        )}
      </div>
    </div>
  );
}
