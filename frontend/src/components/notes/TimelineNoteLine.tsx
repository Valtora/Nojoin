"use client";

import { StickyNote } from "lucide-react";

interface TimelineNoteLineProps {
  noteId: string;
  body: string;
  /** The note's time, written the way the surrounding lines write theirs. */
  timeLabel: string;
  status?: "saved" | "saving" | "failed";
  error?: string | null;
  onRetry?: () => void;
  onDiscard?: () => void;
  /** Width of the time column, matching the transcript it sits in. */
  timeColumnClassName?: string;
}

const ACTION_BUTTON =
  "inline-flex min-h-8 items-center rounded-lg border border-control-border bg-surface-card px-3 text-xs font-semibold text-contrast-muted transition-colors hover:border-action-border hover:text-action-text pointer-coarse:min-h-[44px]";

/**
 * A note in the transcript, on its own line after the line it follows. The
 * same line in the live view and the finished one, so a note reads the same
 * before and after Stop.
 */
export default function TimelineNoteLine({
  noteId,
  body,
  timeLabel,
  status = "saved",
  error = null,
  onRetry,
  onDiscard,
  timeColumnClassName = "shrink-0 pt-0.5 font-mono text-xs tabular-nums text-contrast-icon-muted",
}: TimelineNoteLineProps) {
  return (
    <div
      className="flex gap-3"
      data-testid="timeline-note"
      data-note-id={noteId}
      data-note-status={status}
    >
      <span className={timeColumnClassName}>{timeLabel}</span>
      <div className="min-w-0 flex-1 border-l-2 border-action-border pl-3">
        <div className="flex flex-wrap items-center gap-x-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-action-text">
          <span className="inline-flex items-center gap-1.5">
            <StickyNote className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="sr-only">Your note at {timeLabel}:</span>
            <span aria-hidden="true">Note</span>
          </span>
          {status === "saving" ? (
            <span className="font-normal normal-case tracking-normal text-contrast-helper">
              Saving…
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 whitespace-pre-wrap break-words text-sm leading-6 text-foreground">
          {body}
        </p>
        {status === "failed" ? (
          <p role="alert" className="mt-1 text-xs text-status-danger-fg">
            {error ?? "Not saved."}
          </p>
        ) : null}
        {status === "failed" && (onRetry || onDiscard) ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {onRetry ? (
              <button type="button" onClick={onRetry} className={ACTION_BUTTON}>
                Retry
              </button>
            ) : null}
            {onDiscard ? (
              <button type="button" onClick={onDiscard} className={ACTION_BUTTON}>
                Discard
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
