"use client";

import { AlertTriangle, Pause, Play, Square, Trash2 } from "lucide-react";
import { useState } from "react";

import SpeakerCapField from "@/components/SpeakerCapField";
import { updateRecordingMaxSpeakers } from "@/lib/api";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";

interface LiveMeetingControlsProps {
  size?: "compact" | "full";
  onMeetingEnd?: () => void;
  onMeetingDiscard?: () => void;
}

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

export default function LiveMeetingControls({
  size = "full",
  onMeetingEnd,
  onMeetingDiscard,
}: LiveMeetingControlsProps) {
  const {
    cancel,
    controller,
    coverageWarning,
    elapsedSeconds,
    pause,
    recordingId,
    resume,
    runtimeActive,
    status,
    stop,
  } = useCapture();

  const { addNotification } = useNotificationStore();
  const isRecording = status === "recording";
  // Pause and resume need live browser tracks. Stop and discard do not: a
  // recording whose runtime was torn down must still be finishable, or it is
  // stranded in PAUSED with no route to processing (issue #166).
  const transportDisabled = !runtimeActive || status === "finalizing";
  const stopDisabled = status === "finalizing" || !recordingId;

  // Diarization runs at stop time, so the cap stays editable for the whole
  // recording: whatever is set when capture ends is what gets applied.
  const [maxSpeakers, setMaxSpeakers] = useState<number | null>(null);

  const handleMaxSpeakersCommit = async (next: number | null) => {
    const previous = maxSpeakers;
    setMaxSpeakers(next);
    if (!recordingId) {
      return;
    }
    try {
      await updateRecordingMaxSpeakers(recordingId, next);
    } catch (err: unknown) {
      setMaxSpeakers(previous);
      addNotification({
        type: "error",
        message:
          err instanceof Error && err.message
            ? err.message
            : "Failed to update the speaker limit.",
      });
    }
  };

  // Inline during capture: a full-width field spanned the whole workspace for
  // what is at most a two-character value, and pushed the transcript panel down
  // by three lines of label and hint.
  const speakerCapField = (
    <SpeakerCapField
      value={maxSpeakers}
      onCommit={handleMaxSpeakersCommit}
      disabled={transportDisabled || !recordingId}
      size={size}
      layout="inline"
      liveHint
      idPrefix={`live-speaker-cap-${size}`}
    />
  );

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
      return;
    }
    if (onMeetingDiscard) {
      setTimeout(onMeetingDiscard, 300);
    }
  };

  const sendCommand = async (command: "stop" | "pause" | "resume") => {
    try {
      if (command === "pause") {
        await pause();
        return { ok: true };
      }

      if (command === "resume") {
        await resume();
        return { ok: true };
      }

      return await stop();
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
      return null;
    }
  };

  const handleStop = async () => {
    const result = await sendCommand("stop");
    if (result) {
      if (onMeetingEnd) {
        setTimeout(onMeetingEnd, 300);
      }
    }
  };

  const statusLabel = isRecording ? "Recording" : "Paused";

  // The timer is wall clock, so it keeps counting while a suspended tab records
  // nothing. This badge appears only when the two diverge, naming what was
  // actually captured rather than quietly overstating the recording (issue #166).
  const coverageBadge = coverageWarning ? (
    <div
      className="density-surface-panel flex items-start gap-2 border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200"
      role="status"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        <span className="font-semibold">
          {formatTime(coverageWarning.capturedSeconds)} captured
        </span>{" "}
        of {formatTime(coverageWarning.elapsedSeconds)} elapsed. Audio is being
        lost because this tab is being suspended. Keep the Nojoin tab open and
        the device awake.
      </span>
    </div>
  ) : null;

  if (size === "compact") {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-red-100 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            <span
              className={`inline-block h-2 w-2 rounded-full bg-red-500 ${isRecording ? "animate-pulse" : ""}`}
            />
            <span className="text-xs font-semibold uppercase tracking-[0.14em]">
              {statusLabel}
            </span>
            <span className="ml-auto font-mono text-sm font-semibold text-gray-950 dark:text-white">
              {formatTime(elapsedSeconds)}
            </span>
          </div>
          {isRecording ? (
            <button
              type="button"
              onClick={() => sendCommand("pause")}
              disabled={transportDisabled}
              className="rounded-xl border border-gray-300 bg-white p-2 text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
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
              className="rounded-xl border border-gray-300 bg-white p-2 text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
              title="Resume recording"
              aria-label="Resume recording"
            >
              <Play className="h-4 w-4" />
            </button>
          )}
          <button
            type="button"
            onClick={handleStop}
            disabled={stopDisabled}
            className="rounded-xl bg-red-600 p-2 text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            title="Stop recording"
            aria-label="Stop recording"
          >
            <Square className="h-4 w-4 fill-current" />
          </button>
          <button
            type="button"
            onClick={handleDiscard}
            disabled={stopDisabled}
            className="rounded-xl border border-red-200 bg-white p-2 text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-500/30 dark:bg-gray-950/60 dark:text-red-300 dark:hover:bg-red-500/10"
            title="Discard recording"
            aria-label="Discard recording"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
        {coverageBadge}
        {speakerCapField}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="density-surface-panel flex items-center justify-between gap-4 border border-red-100 bg-red-50 px-4 py-4 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
        <div className="flex items-center gap-3">
          <div
            className={`h-2.5 w-2.5 rounded-full bg-red-500 ${isRecording ? "animate-pulse" : ""}`}
          />
          <span className="text-sm font-semibold uppercase tracking-[0.16em]">
            {statusLabel}
          </span>
        </div>
        <span className="font-mono text-3xl font-semibold text-gray-950 dark:text-white">
          {formatTime(elapsedSeconds)}
        </span>
      </div>

      <div className="flex flex-wrap gap-3">
        {isRecording ? (
          <button
            type="button"
            onClick={() => sendCommand("pause")}
            disabled={transportDisabled}
            className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
            title="Pause recording"
          >
            <Pause className="h-4 w-4" />
            Pause
          </button>
        ) : (
          <button
            type="button"
            onClick={() => sendCommand("resume")}
            disabled={transportDisabled}
            className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
            title="Resume recording"
          >
            <Play className="h-4 w-4" />
            Resume
          </button>
        )}

        <button
          type="button"
          onClick={handleStop}
          disabled={stopDisabled}
          className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-red-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          title="Stop recording"
        >
          <Square className="h-4 w-4 fill-current" />
          Stop
        </button>

        <button
          type="button"
          onClick={handleDiscard}
          disabled={stopDisabled}
          className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-red-200 bg-white px-4 py-3 text-sm font-semibold text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-500/30 dark:bg-gray-950/60 dark:text-red-300 dark:hover:bg-red-500/10"
          title="Discard recording"
        >
          <Trash2 className="h-4 w-4" />
          Discard
        </button>
      </div>

      {coverageBadge}
      {speakerCapField}
    </div>
  );
}
