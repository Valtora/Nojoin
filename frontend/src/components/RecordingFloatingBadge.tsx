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
  const disabled = !runtimeActive || status === "finalizing";

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
      <div className="flex items-center gap-2 rounded-2xl border border-red-200/60 bg-white/95 px-4 py-2.5 shadow-xl shadow-red-900/10 backdrop-blur-md dark:border-red-500/20 dark:bg-gray-900/95 dark:shadow-black/30">
        <button
          type="button"
          onClick={handleNavigate}
          className="flex items-center gap-2"
          title="Go to recording"
        >
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full bg-red-500 ${isRecording ? "animate-pulse" : ""}`}
          />
          <span className="text-xs font-semibold uppercase tracking-[0.14em] text-red-700 dark:text-red-300">
            {isRecording ? "Recording" : "Paused"}
          </span>
          <span className="ml-1 font-mono text-sm font-semibold text-gray-950 dark:text-white">
            {formatTime(elapsedSeconds)}
          </span>
        </button>

        <span className="mx-1 h-5 w-px bg-gray-200 dark:bg-gray-700" />

        {isRecording ? (
          <button
            type="button"
            onClick={() => sendCommand("pause")}
            disabled={disabled}
            className="rounded-lg p-1.5 text-gray-600 transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-50 dark:text-gray-400 dark:hover:bg-red-500/10 dark:hover:text-red-300"
            title="Pause recording"
            aria-label="Pause recording"
          >
            <Pause className="h-4 w-4" />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => sendCommand("resume")}
            disabled={disabled}
            className="rounded-lg p-1.5 text-gray-600 transition-colors hover:bg-green-50 hover:text-green-700 disabled:opacity-50 dark:text-gray-400 dark:hover:bg-green-500/10 dark:hover:text-green-300"
            title="Resume recording"
            aria-label="Resume recording"
          >
            <Play className="h-4 w-4" />
          </button>
        )}

        <button
          type="button"
          onClick={() => sendCommand("stop")}
          disabled={disabled}
          className="rounded-lg bg-red-600 p-1.5 text-white transition-colors hover:bg-red-700 disabled:opacity-50"
          title="Stop recording"
          aria-label="Stop recording"
        >
          <Square className="h-4 w-4 fill-current" />
        </button>

        <button
          type="button"
          onClick={handleDiscard}
          disabled={disabled}
          className="rounded-lg p-1.5 text-gray-600 transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-50 dark:text-gray-400 dark:hover:bg-red-500/10 dark:hover:text-red-300"
          title="Discard recording"
          aria-label="Discard recording"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
