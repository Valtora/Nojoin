"use client";

import { useState } from "react";
import { Mic } from "lucide-react";
import { useRouter } from "next/navigation";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import { useCapture } from "@/lib/capture/CaptureProvider";

import CaptureUnsupportedNotice from "./CaptureUnsupportedNotice";
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
  const { backend } = useServiceStatusStore();
  const {
    error: captureError,
    pausedRecording,
    runtimeActive,
    start,
    status,
    support,
  } = useCapture();

  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const hasLiveRecording =
    runtimeActive && (status === "recording" || status === "paused");
  const hasPausedBlock = Boolean(pausedRecording && !runtimeActive);
  const isBusy = status === "starting" || status === "finalizing";
  const unsupported = !support.supported;

  const meetingSurfaceState: MeetingSurfaceState = !backend
    ? {
        buttonLabel: "Nojoin unavailable",
        buttonMode: "wait",
        buttonDisabled: true,
        buttonTooltip:
          "The Nojoin backend is offline. Wait for it to reconnect before starting a meeting.",
      }
    : unsupported
        ? {
            buttonLabel: "Unsupported browser",
            buttonMode: "wait",
            buttonDisabled: true,
            buttonTooltip:
              "Use a Chromium browser on Windows or Linux for browser capture.",
          }
        : hasPausedBlock
          ? {
              buttonLabel: "Paused recording needs attention",
              buttonMode: "wait",
              buttonDisabled: true,
              buttonTooltip:
                "Resume or discard the paused recording in the modal before starting anything new.",
            }
          : isBusy
            ? {
                buttonLabel:
                  status === "finalizing" ? "Finalizing meeting..." : "Starting meeting...",
                buttonMode: "wait",
                buttonDisabled: true,
                buttonTooltip:
                  status === "finalizing"
                    ? "Nojoin is finalizing the current meeting recording."
                    : "Nojoin is preparing browser capture.",
              }
          : {
              buttonLabel: "Start Meeting",
              buttonMode: "start",
              buttonDisabled: false,
              buttonTooltip: "Start a new meeting recording.",
            };

  const sendStart = async () => {
    setError(null);
    try {
      return await start("");
    } catch (err: unknown) {
      if (err instanceof Error && err.message) {
        setError(err.message);
      } else {
        setError("Failed to start browser recording.");
      }
      return null;
    }
  };

  const handleStart = async () => {
    const response = await sendStart();
    if (response && response.recordingId) {
      router.push(`/recordings/${response.recordingId}`);
      if (onMeetingEnd) onMeetingEnd();
    }
  };

  const handlePrimaryAction = () => {
    setError(null);
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
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                Click Start Meeting to begin browser capture from this dashboard card.
              </p>
            </div>
          </div>

          {unsupported ? (
            <CaptureUnsupportedNotice reason={support.reason} />
          ) : null}

          {(error || captureError) && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
              {error || captureError}
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
        {unsupported ? (
          <div className="mb-2">
            <CaptureUnsupportedNotice reason={support.reason} compact />
          </div>
        ) : null}

        {(error || captureError) ? (
          <div className="mb-2 text-xs text-red-500">{error || captureError}</div>
        ) : null}

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
