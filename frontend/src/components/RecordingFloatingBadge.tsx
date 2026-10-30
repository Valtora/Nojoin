"use client";

import { ButtonBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { Pause, Play, Square, Trash2 } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { useCapture, useCaptureState } from "@/lib/capture/CaptureProvider";
import { CaptureResumeSupersededError } from "@/lib/capture/shared";
import { cn } from "@/lib/cn";
import { useNotificationStore } from "@/lib/notificationStore";

import { TOUCH_TARGET_HEIGHT, TOUCH_TARGET_SQUARE } from "./ui/touchTarget";

const DISCARD_CONFIRM_MESSAGE =
  "Discard this recording? This permanently deletes the in-progress meeting and its audio, and cannot be undone.";

function formatTime(seconds: number) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

// The controls take the bar's full height on a touch screen.
const CONTROL = cn(
  "inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors disabled:opacity-50",
  TOUCH_TARGET_SQUARE,
);

/**
 * The capture's state, clock and controls, on every page but its own
 * recording's. It is a strip across the top of the app shell rather than a
 * card floating over the page, so it covers nothing: the shell gives it a row
 * of its own, and `--capture-bar-h` (set on the shell while the bar is there)
 * tells the full-height nav rail and the recording header's phone menu how
 * far down the page now starts.
 */
export default function RecordingFloatingBadge() {
  const pathname = usePathname();
  const router = useRouter();
  const {
    cancel,
    controller,
    pause,
    resume,
    runtimeActive,
    status,
    stop,
    recordingId,
  } = useCapture();
  const elapsedSeconds = useCaptureState((state) => state.elapsedSeconds);
  const { addNotification } = useNotificationStore();

  const isRecording = status === "recording";
  const show =
    runtimeActive && (status === "recording" || status === "paused");
  // Kept in step with LiveMeetingControls: pause and resume need live browser
  // tracks, stop and discard do not (issue #166).
  const transportDisabled = !runtimeActive || status === "finalizing";
  const stopDisabled = status === "finalizing" || !recordingId;

  const isRecordingDetailPage =
    recordingId && pathname === `/recordings/${recordingId}`;

  if (!show || isRecordingDetailPage) {
    return null;
  }

  const sendCommand = async (command: "stop" | "pause" | "resume") => {
    try {
      if (command === "pause") {
        await pause();
        return;
      }
      if (command === "resume") {
        await resume();
        return;
      }
      await stop();
      if (recordingId) {
        router.push(`/recordings/${recordingId}`);
      }
    } catch (err: unknown) {
      // The Stop or Discard that overtook a Resume reports its own outcome.
      if (!(err instanceof CaptureResumeSupersededError) && !controller.getState().error) {
        addNotification({
          type: "error",
          message:
            err instanceof Error && err.message
              ? err.message
              : `Failed to ${command} the browser recording.`,
        });
      }
    }
  };

  const handleDiscard = async () => {
    if (!window.confirm(DISCARD_CONFIRM_MESSAGE)) {
      return;
    }
    try {
      await cancel();
    } catch (err: unknown) {
      if (!controller.getState().error) {
        addNotification({
          type: "error",
          message:
            err instanceof Error && err.message
              ? err.message
              : "Failed to discard the browser recording.",
        });
      }
    }
  };

  const handleNavigate = () => {
    if (recordingId) {
      router.push(`/recordings/${recordingId}`);
    }
  };

  return (
    <SurfaceBase
      data-ui-fill="card"
      kind="pane"
      data-capture-bar=""
      role="region"
      aria-label="Recording in progress"
      className="flex h-[var(--capture-bar-h)] shrink-0 items-end justify-center border-b border-status-danger-border bg-surface-card px-3 pt-[env(safe-area-inset-top)]"
    >
      <div className="flex h-[44px] min-w-0 items-center gap-2">
        <ButtonBase
          type="button"
          onClick={handleNavigate}
          className={cn("flex items-center gap-2 rounded-lg px-1", TOUCH_TARGET_HEIGHT)}
          title="Go to recording"
        >
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full bg-danger ${isRecording ? "animate-pulse" : ""}`}
          />
          <span className="text-xs font-semibold uppercase tracking-[0.14em] text-status-danger-fg">
            {isRecording ? "Recording" : "Paused"}
          </span>
          <span className="ml-1 font-mono text-sm font-semibold text-foreground">
            {formatTime(elapsedSeconds)}
          </span>
        </ButtonBase>

        <span className="mx-1 h-5 w-px bg-surface-inset" />

        {isRecording ? (
          <ButtonBase
            type="button"
            onClick={() => sendCommand("pause")}
            disabled={transportDisabled}
            className={cn(CONTROL, "text-contrast-helper hover:bg-status-danger-bg hover:text-status-danger-fg")}
            title="Pause recording"
            aria-label="Pause recording"
          >
            <Pause className="h-4 w-4" />
          </ButtonBase>
        ) : (
          <ButtonBase
            type="button"
            onClick={() => sendCommand("resume")}
            disabled={transportDisabled}
            className={cn(CONTROL, "text-contrast-helper hover:bg-status-success-bg hover:text-status-success-fg")}
            title="Resume recording"
            aria-label="Resume recording"
          >
            <Play className="h-4 w-4" />
          </ButtonBase>
        )}

        <ButtonBase
          data-ui-fill="status-danger"
          data-ui-hover="status-danger" chrome="action"
          type="button"
          onClick={() => sendCommand("stop")}
          disabled={stopDisabled}
          className={cn(CONTROL, "bg-status-danger-bg text-foreground hover:bg-status-danger-bg")}
          title="Stop recording"
          aria-label="Stop recording"
        >
          <Square className="h-4 w-4 fill-current" />
        </ButtonBase>

        <ButtonBase
          type="button"
          onClick={handleDiscard}
          disabled={stopDisabled}
          className={cn(CONTROL, "text-contrast-helper hover:bg-status-danger-bg hover:text-status-danger-fg")}
          title="Discard recording"
          aria-label="Discard recording"
        >
          <Trash2 className="h-4 w-4" />
        </ButtonBase>
      </div>
    </SurfaceBase>
  );
}
