"use client";

import { ButtonBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { Mic } from "lucide-react";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useConnectivityStore } from "@/lib/connectivity/monitor";
import { isReachable } from "@/lib/connectivity/reducer";
import { useCapture, useCaptureState } from "@/lib/capture/CaptureProvider";
import { isCaptureInterrupted } from "@/lib/capture/recovery";
import { DEMO_MODE, DEMO_RECORDING_OFF } from "@/lib/demo";
import { useNotificationStore } from "@/lib/notificationStore";

import CaptureUnsupportedNotice from "./CaptureUnsupportedNotice";
import { CaptureNotices } from "./LiveMeetingControls";
import Button from "./ui/Button";
import IconButton from "./ui/IconButton";

type MeetingControlsProps =
  | {
      onMeetingEnd?: () => void;
      variant?: "sidebar";
      /**
       * A second "add a recording" action, laid out beside Start Meeting and
       * under the capture's status while a meeting runs.
       */
      pairedAction?: ReactNode;
    }
  | {
      onMeetingEnd?: () => void;
      variant: "dashboard" | "rail" | "compact";
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
    start,
    status,
    stopStage,
    support,
  } = useCapture();
  const uploadQueue = useCaptureState((state) => state.uploadQueue);

  const { addNotification } = useNotificationStore();
  const router = useRouter();
  const hasLiveRecording =
    runtimeActive && (status === "recording" || status === "paused");
  const hasPausedBlock = Boolean(pausedRecording && (!runtimeActive || status === "error"));
  const isBusy = status === "starting" || status === "finalizing";
  const unsupported = !support.supported;
  const microphoneOnly = support.supported && support.mode === "microphone_only";

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
          ? pausedRecording && isCaptureInterrupted(pausedRecording)
            ? {
                buttonLabel: "Interrupted recording needs attention",
                buttonMode: "wait",
                buttonDisabled: true,
                buttonTooltip: runtimeActive
                  ? "Stop or discard the current recording from its page before starting anything new."
                  : "Resume, stop or discard the interrupted recording in the dialog before starting anything new.",
              }
            : {
                buttonLabel: "Paused recording needs attention",
                buttonMode: "wait",
                buttonDisabled: true,
                buttonTooltip: runtimeActive
                  ? "Stop or discard the current recording from its page before starting anything new."
                  : "Resume or discard the paused recording in the modal before starting anything new.",
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

  // While this tab captures, the capture's controls are on its page and in the
  // floating badge; here it is only named, with its upload and coverage
  // notices, so there is one set of buttons. The time is shown with those
  // controls, and this is no live region: they already announce the state.
  const isRail = variant === "rail";
  const liveLabel = status === "paused" ? "Paused" : "Recording";
  const liveStatus = hasLiveRecording ? (
    <div
      data-testid="capture-status"
      title={isRail ? liveLabel : undefined}
      className={`flex items-center rounded-xl border border-status-danger-border bg-status-danger-bg text-status-danger-fg ${
        isRail ? "h-10 w-10 justify-center" : "gap-2 px-3 py-2"
      }`}
    >
      <span
        aria-hidden="true"
        className={`inline-block h-2 w-2 shrink-0 rounded-full bg-danger ${status === "recording" ? "animate-pulse" : ""}`}
      />
      <span className={isRail ? "sr-only" : "text-xs font-semibold uppercase tracking-[0.14em]"}>
        {liveLabel}
      </span>
    </div>
  ) : null;

  if (variant === "rail") {
    if (liveStatus) return liveStatus;
    return (
      <IconButton
        size="sm"
        icon={<Mic aria-hidden="true" />}
        onClick={handlePrimaryAction}
        disabled={meetingSurfaceState.buttonDisabled}
        title={meetingSurfaceState.buttonTooltip}
        aria-label={meetingSurfaceState.buttonLabel}
        className="text-rail-fg-muted hover:bg-rail-item-hover hover:text-rail-fg"
      />
    );
  }

  if (variant === "compact") {
    if (liveStatus) return liveStatus;
    return (
      <Button
        variant="primary"
        iconLeft={<Mic aria-hidden="true" className="h-4 w-4 shrink-0" />}
        onClick={handlePrimaryAction}
        disabled={meetingSurfaceState.buttonDisabled}
        title={meetingSurfaceState.buttonTooltip}
        aria-label={meetingSurfaceState.buttonLabel}
        className="rounded-md px-3 disabled:bg-action-tint disabled:text-action-tint-fg disabled:opacity-100"
      >
        Start
      </Button>
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
            </div>
          ) : (
            <div className="space-y-3">
              {liveStatus}
              <CaptureNotices />
            </div>
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
                  ? "min-w-fit grow basis-0 whitespace-nowrap"
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
            </div>
            {pairedAction ? (
              <div className="flex min-w-fit grow basis-0">{pairedAction}</div>
            ) : null}
          </div>
        ) : (
          <>
            <div className="space-y-2">
              {liveStatus}
              <CaptureNotices />
            </div>
            {pairedAction ? <div className="mt-2 flex">{pairedAction}</div> : null}
          </>
        )}
      </div>
    </div>
  );
}
