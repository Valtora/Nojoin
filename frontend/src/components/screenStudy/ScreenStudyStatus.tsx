"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { format } from "date-fns";
import { ScanEye, Square, Trash2 } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { useScreenStudyStore, type ScreenStudyStatus as StudyStatus } from "@/lib/capture/screenStudy/store";
import { useNotificationStore } from "@/lib/notificationStore";
import type { RecordingId } from "@/types";

const DELETE_CONFIRM_MESSAGE =
  "Delete this meeting's screen study frames now? Sampling stops, and the recording carries on.";

const STATUS_LABELS: Record<StudyStatus, string> = {
  sampling: "Sampling",
  paused: "Paused",
  stopped: "Stopped",
  deleted: "Frames deleted",
};

export const formatStudyBytes = (bytes: number): string => {
  if (bytes < 1024 * 1024) {
    return `${Math.max(0, Math.round(bytes / 1024))} KB`;
  }
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const formatDeleteDate = (iso: string | null): string | null => {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : format(date, "d MMM yyyy");
};

interface ScreenStudyStatusProps {
  recordingId: RecordingId | null;
  compact?: boolean;
}

/**
 * The live controls' screen study row (#653): how many frames the server
 * holds for this meeting, their size and when they go, with Stop (sampling
 * ends, the recording carries on) and Delete (this meeting's frames go now).
 */
export default function ScreenStudyStatus({ recordingId, compact = false }: ScreenStudyStatusProps) {
  const study = useScreenStudyStore();
  const { stopScreenStudy, deleteScreenStudyFrames } = useCapture();
  const { addNotification } = useNotificationStore();

  if (!study.status || !recordingId || study.recordingId !== recordingId) {
    return null;
  }

  const active = study.status === "sampling" || study.status === "paused";
  const deleteDate = formatDeleteDate(study.deleteAfter);
  const frameWord = study.frames === 1 ? "frame" : "frames";
  const summary =
    study.status === "deleted"
      ? "This meeting's frames have been deleted."
      : study.frames === 0
        ? active
          ? "No frames yet."
          : "No frames were kept."
        : `${study.frames.toLocaleString("en-GB")} ${frameWord}, ${formatStudyBytes(study.bytes)}${
            deleteDate ? `, deleted on ${deleteDate}` : ""
          }`;

  const handleDelete = async () => {
    if (!window.confirm(DELETE_CONFIRM_MESSAGE)) {
      return;
    }
    try {
      await deleteScreenStudyFrames();
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message:
          error instanceof Error && error.message
            ? `The screen study frames could not be deleted: ${error.message}`
            : "The screen study frames could not be deleted.",
      });
    }
  };

  const buttonClass =
    "inline-flex items-center gap-1.5 rounded-md border bg-surface-card px-2.5 py-1 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <div
      className="density-surface-panel flex flex-col gap-2 border border-status-info-border bg-status-info-bg px-3 py-2 text-xs text-status-info-fg"
      role="status"
      aria-label="Screen study"
    >
      <div className="flex items-start gap-2">
        <ScanEye className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="font-semibold">Screen study: {STATUS_LABELS[study.status]}.</span>{" "}
          <span>{summary}</span>
          {study.dropped > 0 && !compact ? (
            <span className="block">
              {study.dropped} {study.dropped === 1 ? "frame was" : "frames were"} dropped while uploads were behind.
            </span>
          ) : null}
          {study.error ? <span className="block">{study.error}</span> : null}
        </span>
      </div>
      {study.status !== "deleted" ? (
        <div className="flex flex-wrap gap-2">
          {active ? (
            <ButtonBase
              chrome="action"
              type="button"
              onClick={stopScreenStudy}
              className={`${buttonClass} border-control-border text-contrast-muted hover:border-action-border hover:text-action-text`}
              aria-label="Stop the screen study and keep recording"
            >
              <Square className="h-3 w-3 fill-current" aria-hidden="true" />
              Stop sampling
            </ButtonBase>
          ) : null}
          <ButtonBase
            chrome="action"
            type="button"
            onClick={() => void handleDelete()}
            disabled={study.deleting || (study.frames === 0 && study.queued === 0 && !active)}
            className={`${buttonClass} border-status-danger-border text-status-danger-fg hover:bg-status-danger-bg`}
            aria-label="Delete this meeting's screen study frames"
          >
            <Trash2 className="h-3 w-3" aria-hidden="true" />
            {study.deleting ? "Deleting" : "Delete frames"}
          </ButtonBase>
        </div>
      ) : null}
    </div>
  );
}
