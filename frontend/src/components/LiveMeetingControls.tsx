"use client";

import { AlertTriangle, Pause, Play, Square, Trash2, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import SpeakerCapField from "@/components/SpeakerCapField";
import { updateRecordingMaxSpeakers } from "@/lib/api";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";

interface LiveMeetingControlsProps {
  /**
   * `compact` is the rail's icon-only row, `full` the stacked card, and `bar`
   * the recording workspace's toolbar: one line carrying the state, the clock,
   * the transport and the speaker cap, so the columns beneath it keep their
   * width for the panels that need it.
   */
  size?: "compact" | "full" | "bar";
  onMeetingEnd?: () => void;
  onMeetingDiscard?: () => void;
  /** Extra controls for the `bar` variant, placed before the speaker cap. */
  barTrailing?: ReactNode;
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
  barTrailing,
}: LiveMeetingControlsProps) {
  const {
    cancel,
    controller,
    coverageWarning,
    dismissCoverageWarning,
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
      // The bar takes the standard control height: `compact` is the rail's
      // icon-only row, and this is a toolbar with room for a normal field.
      size={size === "compact" ? "compact" : "full"}
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

  // The timer is wall clock, so it keeps counting whether or not audio is
  // reaching the server. This badge appears only when the two diverge, naming
  // what the server actually holds rather than quietly overstating the
  // recording (issue #166).
  //
  // The cause comes from the controller, which has the evidence to tell a
  // suspended tab from an unreachable backend. This used to assert suspension
  // unconditionally, which sent people to check browser settings during a
  // server-side outage.
  const coverageBadge = coverageWarning ? (
    <div
      className="density-surface-panel flex items-start gap-2 border border-status-warning-border bg-status-warning-bg px-3 py-2 text-xs text-status-warning-fg"
      role="status"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span className="flex-1">
        <span className="font-semibold">
          {formatTime(coverageWarning.capturedSeconds)} captured
        </span>{" "}
        of {formatTime(coverageWarning.elapsedSeconds)} elapsed.{" "}
        {coverageWarning.cause === "backend-unreachable"
          ? "Nojoin cannot reach the server at the moment. Recording is continuing, and queued audio uploads when the connection returns."
          : coverageWarning.cause === "tab-suspended"
            ? "Audio is being lost because this tab is being suspended. Keep the Nojoin tab open and the device awake."
            : "Keep the Nojoin tab open and the device awake, and check your connection to the Nojoin server."}
      </span>
      <button
        type="button"
        onClick={dismissCoverageWarning}
        className="-mr-1 -mt-1 shrink-0 rounded p-1 text-status-warning-fg/70 transition-colors hover:bg-status-warning-border/40 hover:text-status-warning-fg"
        aria-label="Dismiss the capture coverage warning"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  ) : null;

  if (size === "compact") {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-status-danger-border bg-status-danger-bg px-3 py-2 text-status-danger-fg">
            <span
              className={`inline-block h-2 w-2 rounded-full bg-danger ${isRecording ? "animate-pulse" : ""}`}
            />
            <span className="text-xs font-semibold uppercase tracking-[0.14em]">
              {statusLabel}
            </span>
            <span className="ml-auto font-mono text-sm font-semibold text-foreground">
              {formatTime(elapsedSeconds)}
            </span>
          </div>
          {isRecording ? (
            <button
              type="button"
              onClick={() => sendCommand("pause")}
              disabled={transportDisabled}
              className="rounded-xl border border-control-border bg-surface-card p-2 text-contrast-muted transition-colors hover:border-action-border hover:text-action-text disabled:cursor-not-allowed disabled:opacity-50"
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
              className="rounded-xl border border-control-border bg-surface-card p-2 text-contrast-muted transition-colors hover:border-action-border hover:text-action-text disabled:cursor-not-allowed disabled:opacity-50"
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
            className="rounded-xl bg-status-danger-bg p-2 text-foreground transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
            title="Stop recording"
            aria-label="Stop recording"
          >
            <Square className="h-4 w-4 fill-current" />
          </button>
          <button
            type="button"
            onClick={handleDiscard}
            disabled={stopDisabled}
            className="rounded-xl border border-status-danger-border bg-surface-card p-2 text-status-danger-fg transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
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

  if (size === "bar") {
    // The transport shares the row's slack rather than sitting at its intrinsic
    // width: at three columns there was a great deal of empty bar to the right
    // of three small buttons. Capped, because a Stop button half a metre wide
    // is not an improvement.
    const barButtonClass =
      "inline-flex h-10 min-w-[7rem] flex-1 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50";

    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <div className="density-surface-panel flex shrink-0 items-center gap-3 bg-status-danger-bg px-3 py-1.5 text-status-danger-fg">
            <span
              className={`h-2.5 w-2.5 rounded-full bg-danger ${isRecording ? "animate-pulse" : ""}`}
            />
            <span className="text-xs font-semibold uppercase tracking-[0.16em]">
              {statusLabel}
            </span>
            <span className="font-mono text-xl font-semibold tabular-nums text-foreground">
              {formatTime(elapsedSeconds)}
            </span>
          </div>

          <div className="flex min-w-[18rem] max-w-[34rem] flex-1 flex-wrap items-center gap-2">
            {isRecording ? (
              <button
                type="button"
                onClick={() => sendCommand("pause")}
                disabled={transportDisabled}
                className={`${barButtonClass} border border-control-border bg-surface-card text-contrast-muted hover:border-action-border hover:text-action-text`}
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
                className={`${barButtonClass} border border-control-border bg-surface-card text-contrast-muted hover:border-action-border hover:text-action-text`}
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
              className={`${barButtonClass} bg-status-danger-bg text-foreground hover:bg-status-danger-bg`}
              title="Stop recording"
            >
              <Square className="h-4 w-4 fill-current" />
              Stop
            </button>

            <button
              type="button"
              onClick={handleDiscard}
              disabled={stopDisabled}
              className={`${barButtonClass} border border-status-danger-border bg-surface-card text-status-danger-fg hover:bg-status-danger-bg`}
              title="Discard recording"
            >
              <Trash2 className="h-4 w-4" />
              Discard
            </button>
          </div>

          <div className="flex shrink-0 items-center gap-3">
            {barTrailing}
            {speakerCapField}
          </div>
        </div>

        {coverageBadge}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="density-surface-panel flex items-center justify-between gap-4 border border-status-danger-border bg-status-danger-bg px-4 py-4 text-status-danger-fg">
        <div className="flex items-center gap-3">
          <div
            className={`h-2.5 w-2.5 rounded-full bg-danger ${isRecording ? "animate-pulse" : ""}`}
          />
          <span className="text-sm font-semibold uppercase tracking-[0.16em]">
            {statusLabel}
          </span>
        </div>
        <span className="font-mono text-3xl font-semibold text-foreground">
          {formatTime(elapsedSeconds)}
        </span>
      </div>

      <div className="flex flex-wrap gap-3">
        {isRecording ? (
          <button
            type="button"
            onClick={() => sendCommand("pause")}
            disabled={transportDisabled}
            className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-control-border bg-surface-card px-4 py-3 text-sm font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text disabled:cursor-not-allowed disabled:opacity-50"
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
            className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-control-border bg-surface-card px-4 py-3 text-sm font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text disabled:cursor-not-allowed disabled:opacity-50"
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
          className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-status-danger-bg px-4 py-3 text-sm font-semibold text-foreground transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
          title="Stop recording"
        >
          <Square className="h-4 w-4 fill-current" />
          Stop
        </button>

        <button
          type="button"
          onClick={handleDiscard}
          disabled={stopDisabled}
          className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-status-danger-border bg-surface-card px-4 py-3 text-sm font-semibold text-status-danger-fg transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
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
