"use client";

import { Pause, Play, Square, Trash2 } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";

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

export default function RecordingFloatingBadge() {
  const pathname = usePathname();
  const router = useRouter();
  const {
    cancel,
    controller,
    elapsedSeconds,
    pause,
    resume,
    runtimeActive,
    status,
    stop,
    recordingId,
  } = useCapture();
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
      if (!controller.getState().error) {
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
    <div className="fixed top-6 left-1/2 z-50 -translate-x-1/2">
      <div className="flex items-center gap-2 rounded-2xl border border-status-danger-border bg-surface-card px-4 py-2.5 shadow-float">
        <button
          type="button"
          onClick={handleNavigate}
          className="flex items-center gap-2"
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
        </button>

        <span className="mx-1 h-5 w-px bg-surface-inset" />

        {isRecording ? (
          <button
            type="button"
            onClick={() => sendCommand("pause")}
            disabled={transportDisabled}
            className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-status-danger-bg hover:text-status-danger-fg disabled:opacity-50"
            title="Pause recording"
            aria-label="Pause recording"
          >
            <Pause className="h-4 w-4" />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => sendCommand("resume")}
            disabled={transportDisabled}
            className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-status-success-bg hover:text-status-success-fg disabled:opacity-50"
            title="Resume recording"
            aria-label="Resume recording"
          >
            <Play className="h-4 w-4" />
          </button>
        )}

        <button
          type="button"
          onClick={() => sendCommand("stop")}
          disabled={stopDisabled}
          className="rounded-lg bg-status-danger-bg p-1.5 text-foreground transition-colors hover:bg-status-danger-bg disabled:opacity-50"
          title="Stop recording"
          aria-label="Stop recording"
        >
          <Square className="h-4 w-4 fill-current" />
        </button>

        <button
          type="button"
          onClick={handleDiscard}
          disabled={stopDisabled}
          className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-status-danger-bg hover:text-status-danger-fg disabled:opacity-50"
          title="Discard recording"
          aria-label="Discard recording"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
