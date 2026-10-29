"use client";

import { ButtonBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { Mic } from "lucide-react";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useConnectivityStore } from "@/lib/connectivity/monitor";
import { isReachable } from "@/lib/connectivity/reducer";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { DEMO_MODE, DEMO_RECORDING_OFF } from "@/lib/demo";
import { useNotificationStore } from "@/lib/notificationStore";
import { RecordingStatus } from "@/types";

import CaptureUnsupportedNotice from "./CaptureUnsupportedNotice";
import LiveMeetingControls from "./LiveMeetingControls";
import MeetingCaptureModeSelect from "./MeetingCaptureModeSelect";
import IconButton from "./ui/IconButton";

type MeetingControlsProps =
  | {
      onMeetingEnd?: () => void;
      variant?: "sidebar";
      /**
       * A second "add a recording" action, laid out beside Start Meeting and
       * under the live controls while a meeting runs.
       */
      pairedAction?: ReactNode;
    }
  | {
      onMeetingEnd?: () => void;
      variant: "dashboard" | "rail";
      pairedAction?: never;
    };

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
  pairedAction,
}: MeetingControlsProps) {
  const backend = useConnectivityStore((state) => isReachable(state.status));
  const {
    controller,
    finalizeRetry,
    pausedRecording,
    runtimeActive,
    settings,
    start,
    status,
    stopStage,
    support,
    uploadQueue,
  } = useCapture();

  const { addNotification } = useNotificationStore();
  const router = useRouter();
  const hasLiveRecording =
    runtimeActive && (status === "recording" || status === "paused");
  const hasPausedBlock = Boolean(pausedRecording && !runtimeActive);
  const isBusy = status === "starting" || status === "finalizing";
  const unsupported = !support.supported;
  const microphoneOnly = support.supported && support.mode === "microphone_only";
  const canChooseMode = support.supported && support.mode === "shared_audio";

  // Naming the stage keeps a slow stop legible instead of looking hung.
  const stopStageLabel = !stopStage
    ? null
    : stopStage === "stopping-recorder"
      ? "stopping the recorder"
      : stopStage === "flushing-uploads"
        ? uploadQueue.segments > 0
          ? `uploading the last ${Math.ceil(uploadQueue.seconds)} s of audio`
          : "uploading the last segments"
        : stopStage === "releasing-media"
          ? "releasing the microphone"
          : "finalizing";

  const meetingSurfaceState: MeetingSurfaceState = DEMO_MODE
    ? {
        buttonLabel: "Start Meeting",
        buttonMode: "wait",
        buttonDisabled: true,
        buttonTooltip: DEMO_RECORDING_OFF,
      }
    : !backend
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
          ? pausedRecording?.status === RecordingStatus.UPLOADING
            ? {
                buttonLabel: "Interrupted recording needs attention",
                buttonMode: "wait",
                buttonDisabled: true,
                buttonTooltip:
                  "Resume, stop or discard the interrupted recording in the dialog before starting anything new.",
              }
            : {
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
          : runtimeActive
            ? {
                buttonLabel: "Meeting in progress",
                buttonMode: "wait",
                buttonDisabled: true,
                buttonTooltip:
                  "A meeting recording is already running. Use the recording controls to pause or stop it.",
              }
          : {
              buttonLabel: "Start Meeting",
              buttonMode: "start",
              buttonDisabled: false,
              buttonTooltip: microphoneOnly
                ? "Start a phone microphone recording."
                : settings.startMode === "microphone_only"
                  ? "Start a microphone-only recording without sharing a tab or screen."
                  : "Start a new meeting recording.",
            };

  const modeSelect = canChooseMode ? (
    <MeetingCaptureModeSelect disabled={meetingSurfaceState.buttonDisabled} />
  ) : null;

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

  if (variant === "rail") {
    return (
      <div className="flex flex-col items-center gap-1">
        <IconButton
          size="sm"
          icon={<Mic aria-hidden="true" />}
          onClick={handlePrimaryAction}
          disabled={meetingSurfaceState.buttonDisabled}
          title={meetingSurfaceState.buttonTooltip}
          aria-label={meetingSurfaceState.buttonLabel}
          className="text-rail-fg-muted hover:bg-rail-item-hover hover:text-rail-fg"
        />
        {hasLiveRecording ? null : modeSelect}
      </div>
    );
  }

  if (variant === "dashboard") {
    return (
      <SurfaceBase data-ui-fill="card" data-ui-border="full" kind="card" className="density-surface border border-action-border bg-surface-card shadow-card">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <Mic className="h-5 w-5 shrink-0 text-action-text" />
            <h2 className="text-base font-semibold text-foreground">Meet Now</h2>
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
            // The label is sized from the button's own width rather than fixed
            // at `text-sm`, because this button is as wide as whichever column
            // it lands in: full bleed on a phone, a third of an ultrawide
            // otherwise. `@container` here rather than higher up so the query
            // resolves against the button rather than against the dashboard
            // grid, and the icon is sized in `em` so it tracks the label
            // instead of needing its own scale.
            <div className="@container">
              <ButtonBase
                data-ui-fill="action"
                data-ui-hover="action-hover"
                chrome="action"
                type="button"
                onClick={handlePrimaryAction}
                disabled={meetingSurfaceState.buttonDisabled}
                title={meetingSurfaceState.buttonTooltip}
                aria-label={meetingSurfaceState.buttonLabel}
                className="density-control-lg flex w-full items-center justify-center gap-[0.6em] rounded-2xl bg-action px-4 py-3 text-[clamp(0.875rem,4.5cqw,1.75rem)] font-semibold text-action-on transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-action-tint"
              >
                <Mic className="h-[1.15em] w-[1.15em] shrink-0" />
                {meetingSurfaceState.buttonLabel}
              </ButtonBase>
              {canChooseMode ? (
                <div className="mt-3">
                  <MeetingCaptureModeSelect labelled disabled={meetingSurfaceState.buttonDisabled} />
                </div>
              ) : null}
            </div>
          ) : (
            <LiveMeetingControls size="full" onMeetingEnd={onMeetingEnd} />
          )}
        </div>
      </SurfaceBase>
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
          // The paired action sits beside Start Meeting only while both labels
          // fit on one line each, and wraps to its own line otherwise. A
          // status label (finalizing, unsupported, paused) takes the full
          // width, where it can wrap.
          <div className="flex flex-wrap gap-2">
            <div
              className={
                meetingSurfaceState.buttonMode === "start"
                  ? "flex min-w-fit grow basis-0 items-center gap-2 whitespace-nowrap"
                  : "basis-full"
              }
            >
              <ButtonBase
                data-ui-fill="action"
                data-ui-hover="action-hover"
                chrome="action"
                type="button"
                onClick={handlePrimaryAction}
                disabled={meetingSurfaceState.buttonDisabled}
                title={meetingSurfaceState.buttonTooltip}
                aria-label={meetingSurfaceState.buttonLabel}
                className="flex w-full items-center justify-center gap-2 rounded-md bg-action px-4 py-2 font-medium text-action-on transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-action-tint"
              >
                <Mic className="h-4 w-4" />
                {meetingSurfaceState.buttonLabel}
              </ButtonBase>
              {modeSelect}
            </div>
            {pairedAction ? (
              <div className="flex min-w-fit grow basis-0">{pairedAction}</div>
            ) : null}
          </div>
        ) : (
          <>
            <LiveMeetingControls size="compact" onMeetingEnd={onMeetingEnd} />
            {pairedAction ? <div className="mt-2 flex">{pairedAction}</div> : null}
          </>
        )}
      </div>
    </div>
  );
}
