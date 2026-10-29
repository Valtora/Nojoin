"use client";

import { ButtonBase } from '@/components/ui/nativeBases';
import { AlertTriangle, CloudUpload, Pause, Play, Square, Trash2, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import SpeakerCapField from "@/components/SpeakerCapField";
import { updateRecordingMaxSpeakers } from "@/lib/api";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { CaptureResumeSupersededError } from "@/lib/capture/shared";
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

// Below this the queue is ordinary upload latency, a segment or two.
const UPLOAD_BACKLOG_NOTICE_SECONDS = 30;

const DISCARD_CONFIRM_MESSAGE =
  "Discard this recording? This permanently deletes the in-progress meeting and its audio, and cannot be undone.";

// Floored to whole seconds before anything is split out of it. The captured
// figure comes from the server as a sum of segment durations in milliseconds,
// so it arrives fractional, and `seconds % 60` on 3011.1 renders as
// "50:11.099999999999909" in the coverage badge.
function formatTime(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
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
    ownedRecordingId,
    pausedRecording,
    resume,
    runtimeActive,
    status,
    stop,
    stopStage,
    uploadQueue,
  } = useCapture();

  const { addNotification } = useNotificationStore();
  const isRecording = status === "recording";
  // A conflict's ID is not ownership. A retained runtime can retry a failed
  // stop, and an eligible recovery offer can finish without browser tracks.
  const transportDisabled = !runtimeActive || status === "finalizing";
  const canControl = Boolean(recordingId &&
    (ownedRecordingId === recordingId || pausedRecording?.id === recordingId));
  const stopDisabled = status === "finalizing" || !canControl;

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

  // Inline during capture: the value is at most two characters, and a
  // full-width field would push the transcript panel down.
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

  // A stop can spend minutes sending queued audio, and calling that "Paused"
  // made a working stop look stuck (issue #417).
  const isFinalizing = status === "finalizing";
  const statusLabel = isFinalizing
    ? stopStage === "flushing-uploads" && uploadQueue.segments > 0
      ? "Uploading"
      : "Finishing"
    : isRecording
      ? "Recording"
      : "Paused";

  const showUploadBacklog = isFinalizing
    ? uploadQueue.segments > 0
    : uploadQueue.seconds >= UPLOAD_BACKLOG_NOTICE_SECONDS;
  const uploadBacklogNotice = showUploadBacklog ? (
    <div
      className="density-surface-panel flex items-start gap-2 border border-status-info-border bg-status-info-bg px-3 py-2 text-xs text-status-info-fg"
      role="status"
    >
      <CloudUpload className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span className="flex-1">
        {isFinalizing ? (
          <>
            <span className="font-semibold">
              Uploading the last {formatTime(uploadQueue.seconds)} of audio
            </span>{" "}
            before processing starts. Keep this tab open until it finishes.
          </>
        ) : (
          <>
            <span className="font-semibold">
              Uploads are {formatTime(uploadQueue.seconds)} behind.
            </span>{" "}
            The audio is kept in this browser and sent as the connection allows,
            so keep this tab open.
          </>
        )}
      </span>
    </div>
  ) : null;

  // The timer is wall clock, so it keeps counting whether or not audio is
  // reaching the server. This badge appears only when the two diverge, naming
  // what the server actually holds rather than quietly overstating the
  // recording (issue #166).
  //
  // The cause comes from the controller, which has the evidence to tell a
  // suspended tab from an unreachable backend.
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
        {coverageWarning.queuedSeconds > 0 ? (
          <>
            Another {formatTime(coverageWarning.queuedSeconds)} is recorded and
            waiting to upload.{" "}
          </>
        ) : null}
        {coverageWarning.cause === "backend-unreachable"
          ? "Nojoin cannot reach the server at the moment. Recording is continuing, and queued audio uploads when the connection returns."
          : coverageWarning.cause === "tab-suspended"
            ? "Audio is being lost because this tab is being suspended. Keep the Nojoin tab open and the device awake."
            : "Keep the Nojoin tab open and the device awake, and check your connection to the Nojoin server."}
      </span>
      <ButtonBase
        type="button"
        onClick={dismissCoverageWarning}
        className="-mr-1 -mt-1 shrink-0 rounded p-1 text-status-warning-fg/70 transition-colors hover:bg-status-warning-border/40 hover:text-status-warning-fg"
        aria-label="Dismiss the capture coverage warning"
      >
        <X className="h-3.5 w-3.5" />
      </ButtonBase>
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
            <ButtonBase
              data-ui-fill="card"
              data-ui-border="full" chrome="action"
              type="button"
              onClick={() => sendCommand("pause")}
              disabled={transportDisabled}
              className="rounded-xl border border-control-border bg-surface-card p-2 text-contrast-muted transition-colors hover:border-action-border hover:text-action-text disabled:cursor-not-allowed disabled:opacity-50"
              title="Pause recording"
              aria-label="Pause recording"
            >
              <Pause className="h-4 w-4" />
            </ButtonBase>
          ) : (
            <ButtonBase
              data-ui-fill="card"
              data-ui-border="full" chrome="action"
              type="button"
              onClick={() => sendCommand("resume")}
              disabled={transportDisabled}
              className="rounded-xl border border-control-border bg-surface-card p-2 text-contrast-muted transition-colors hover:border-action-border hover:text-action-text disabled:cursor-not-allowed disabled:opacity-50"
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
            onClick={handleStop}
            disabled={stopDisabled}
            className="rounded-xl bg-status-danger-bg p-2 text-foreground transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
            title="Stop recording"
            aria-label="Stop recording"
          >
            <Square className="h-4 w-4 fill-current" />
          </ButtonBase>
          <ButtonBase
            data-ui-fill="card"
            data-ui-hover="status-danger"
            data-ui-border="full" chrome="action"
            type="button"
            onClick={handleDiscard}
            disabled={stopDisabled}
            className="rounded-xl border border-status-danger-border bg-surface-card p-2 text-status-danger-fg transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
            title="Discard recording"
            aria-label="Discard recording"
          >
            <Trash2 className="h-4 w-4" />
          </ButtonBase>
        </div>
        {coverageBadge}
        {uploadBacklogNotice}
        {speakerCapField}
      </div>
    );
  }

  if (size === "bar") {
    // The transport shares the row's slack rather than sitting at its intrinsic
    // width, capped so the buttons never grow absurdly wide.
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

          {/* The floor stops the transport being squeezed beside the clock,
              but never reaches past the card: a 360px phone leaves it about
              17rem (#538). */}
          <div className="flex min-w-[min(18rem,100%)] max-w-[34rem] flex-1 flex-wrap items-center gap-2">
            {isRecording ? (
              <ButtonBase
                data-ui-fill="card"
                data-ui-border="full" chrome="action"
                type="button"
                onClick={() => sendCommand("pause")}
                disabled={transportDisabled}
                className={`${barButtonClass} border border-control-border bg-surface-card text-contrast-muted hover:border-action-border hover:text-action-text`}
                title="Pause recording"
              >
                <Pause className="h-4 w-4" />
                Pause
              </ButtonBase>
            ) : (
              <ButtonBase
                data-ui-fill="card"
                data-ui-border="full" chrome="action"
                type="button"
                onClick={() => sendCommand("resume")}
                disabled={transportDisabled}
                className={`${barButtonClass} border border-control-border bg-surface-card text-contrast-muted hover:border-action-border hover:text-action-text`}
                title="Resume recording"
              >
                <Play className="h-4 w-4" />
                Resume
              </ButtonBase>
            )}

            <ButtonBase
              data-ui-fill="status-danger"
              data-ui-hover="status-danger" chrome="action"
              type="button"
              onClick={handleStop}
              disabled={stopDisabled}
              className={`${barButtonClass} bg-status-danger-bg text-foreground hover:bg-status-danger-bg`}
              title="Stop recording"
            >
              <Square className="h-4 w-4 fill-current" />
              Stop
            </ButtonBase>

            <ButtonBase
              data-ui-fill="card"
              data-ui-hover="status-danger"
              data-ui-border="full" chrome="action"
              type="button"
              onClick={handleDiscard}
              disabled={stopDisabled}
              className={`${barButtonClass} border border-status-danger-border bg-surface-card text-status-danger-fg hover:bg-status-danger-bg`}
              title="Discard recording"
            >
              <Trash2 className="h-4 w-4" />
              Discard
            </ButtonBase>
          </div>

          {/* Attach Docs and the speaker cap together are wider than the card
              on a phone (#538), so they wrap. The row places this group by its
              full width, so it moves to a line of its own before its contents
              wrap. */}
          <div className="flex flex-wrap items-center gap-3">
            {barTrailing}
            {speakerCapField}
          </div>
        </div>

        {coverageBadge}
        {uploadBacklogNotice}
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
          <ButtonBase
            data-ui-fill="card"
            data-ui-border="full" chrome="action"
            type="button"
            onClick={() => sendCommand("pause")}
            disabled={transportDisabled}
            className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-control-border bg-surface-card px-4 py-3 text-sm font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text disabled:cursor-not-allowed disabled:opacity-50"
            title="Pause recording"
          >
            <Pause className="h-4 w-4" />
            Pause
          </ButtonBase>
        ) : (
          <ButtonBase
            data-ui-fill="card"
            data-ui-border="full" chrome="action"
            type="button"
            onClick={() => sendCommand("resume")}
            disabled={transportDisabled}
            className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-control-border bg-surface-card px-4 py-3 text-sm font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text disabled:cursor-not-allowed disabled:opacity-50"
            title="Resume recording"
          >
            <Play className="h-4 w-4" />
            Resume
          </ButtonBase>
        )}

        <ButtonBase
          data-ui-fill="status-danger"
          data-ui-hover="status-danger" chrome="action"
          type="button"
          onClick={handleStop}
          disabled={stopDisabled}
          className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-status-danger-bg px-4 py-3 text-sm font-semibold text-foreground transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
          title="Stop recording"
        >
          <Square className="h-4 w-4 fill-current" />
          Stop
        </ButtonBase>

        <ButtonBase
          data-ui-fill="card"
          data-ui-hover="status-danger"
          data-ui-border="full" chrome="action"
          type="button"
          onClick={handleDiscard}
          disabled={stopDisabled}
          className="density-control-lg inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-status-danger-border bg-surface-card px-4 py-3 text-sm font-semibold text-status-danger-fg transition-colors hover:bg-status-danger-bg disabled:cursor-not-allowed disabled:opacity-50"
          title="Discard recording"
        >
          <Trash2 className="h-4 w-4" />
          Discard
        </ButtonBase>
      </div>

      {coverageBadge}
      {uploadBacklogNotice}
      {speakerCapField}
    </div>
  );
}
