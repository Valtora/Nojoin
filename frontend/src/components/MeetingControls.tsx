"use client";

import { Mic } from "lucide-react";
import { useRouter } from "next/navigation";
import { useConnectivityStore } from "@/lib/connectivity/monitor";
import { isReachable } from "@/lib/connectivity/reducer";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";

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
  const backend = useConnectivityStore((state) => isReachable(state.status));
  const {
    controller,
    finalizeRetry,
    pausedRecording,
    runtimeActive,
    start,
    status,
    stopStage,
    support,
  } = useCapture();

  const { addNotification } = useNotificationStore();
  const router = useRouter();
  const hasLiveRecording =
    runtimeActive && (status === "recording" || status === "paused");
  const hasPausedBlock = Boolean(pausedRecording && !runtimeActive);
  const isBusy = status === "starting" || status === "finalizing";
  const unsupported = !support.supported;
  const microphoneOnly = support.supported && support.mode === "microphone_only";

  // Naming the stage keeps a slow stop legible instead of looking hung.
  const stopStageLabel = !stopStage
    ? null
    : stopStage === "stopping-recorder"
      ? "stopping the recorder"
      : stopStage === "flushing-uploads"
        ? "uploading the last segments"
        : stopStage === "releasing-media"
          ? "releasing the microphone"
          : "finalizing";

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
              "Use Chrome on desktop for browser capture, or Chrome on Android/iOS for microphone-only capture.",
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
                  status === "finalizing"
                    ? finalizeRetry
                      ? `Finalizing meeting (waiting ${finalizeRetry.attempt}/${finalizeRetry.maxAttempts})...`
                      : stopStageLabel
                        ? `Ending meeting (${stopStageLabel})...`
                        : "Finalizing meeting..."
                    : "Starting meeting...",
                buttonMode: "wait",
                buttonDisabled: true,
                buttonTooltip:
                  status === "finalizing"
                    ? finalizeRetry
                      ? `Nojoin is waiting for the last uploaded segments to be processed (attempt ${finalizeRetry.attempt} of ${finalizeRetry.maxAttempts}).`
                      : stopStageLabel
                        ? `Nojoin is ${stopStageLabel} before queueing processing.`
                        : "Nojoin is finalizing the current meeting recording."
                    : "Nojoin is preparing browser capture.",
              }
          : {
              buttonLabel: "Start Meeting",
              buttonMode: "start",
              buttonDisabled: false,
              buttonTooltip: microphoneOnly
                ? "Start a phone microphone recording."
                : "Start a new meeting recording.",
            };

  const sendStart = async () => {
    try {
      return await start("");
    } catch (err: unknown) {
      console.error("Failed to start browser recording:", err);
      if (!controller.getState().error) {
        addNotification({
          type: "error",
          message:
            err instanceof Error && err.message
              ? err.message
              : err
                ? String(err)
                : "Failed to start browser recording.",
        });
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
    if (meetingSurfaceState.buttonMode === "start") {
      void handleStart();
    }
  };

  if (variant === "dashboard") {
    return (
      <div className="density-surface border border-action-border bg-surface-card shadow-card">
        <div className="flex flex-col gap-5">
          <div className="mt-2 flex items-start gap-3">
            <div className="rounded-2xl bg-action-tint p-2 text-action-text">
              <Mic className="h-5 w-5" />
            </div>
            <div>
              <h2 className="density-heading-section text-2xl font-semibold text-foreground">
                Meet Now
              </h2>
              <p className="mt-1 text-sm text-contrast-helper">
                Click Start Meeting to begin browser capture from this dashboard card.
              </p>
            </div>
          </div>

          {unsupported ? (
            <CaptureUnsupportedNotice reason={support.reason} />
          ) : null}

          {microphoneOnly ? (
            <div className="rounded-2xl border border-status-info-border bg-status-info-bg px-4 py-3 text-sm text-status-info-fg">
              <p className="font-medium">Phone microphone recording</p>
              <p className="mt-1 leading-5 opacity-90">
                Mobile Chrome records the phone microphone only. Keep this tab open and the phone awake.
              </p>
            </div>
          ) : null}

          {!hasLiveRecording ? (
            <button
              type="button"
              onClick={handlePrimaryAction}
              disabled={meetingSurfaceState.buttonDisabled}
              title={meetingSurfaceState.buttonTooltip}
              aria-label={meetingSurfaceState.buttonLabel}
              className="density-control-lg flex items-center justify-center gap-2 rounded-2xl bg-action px-4 py-3 text-sm font-semibold text-action-on transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-action-tint"
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
    <div className="border-b border-action-border bg-transparent p-4">
      <div className="w-full">
        {unsupported ? (
          <div className="mb-2">
            <CaptureUnsupportedNotice reason={support.reason} compact />
          </div>
        ) : null}

        {microphoneOnly ? (
          <div className="mb-2 rounded-lg border border-status-info-border bg-status-info-bg px-3 py-2 text-xs text-status-info-fg">
            Phone microphone only. Keep this tab open.
          </div>
        ) : null}

        {!hasLiveRecording ? (
          <button
            type="button"
            onClick={handlePrimaryAction}
            disabled={meetingSurfaceState.buttonDisabled}
            title={meetingSurfaceState.buttonTooltip}
            aria-label={meetingSurfaceState.buttonLabel}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-action px-4 py-2 font-medium text-action-on transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-action-tint"
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
