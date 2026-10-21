"use client";

import { useRef } from "react";
import { StickyNote } from "lucide-react";

import { noteSnippet } from "@/lib/notes/noteOutbox";

import Button from "../ui/Button";

import { NOTE_BUTTON_TOUCH } from "./noteButtons";

interface TimelineNoteLineProps {
  noteId: string;
  body: string;
  /** The note's time, written the way the surrounding lines write theirs. */
  timeLabel: string;
  status?: "saved" | "saving" | "failed";
  error?: string | null;
  onRetry?: () => void;
  /** Removes the line: the caller moves focus somewhere that stays. */
  onDiscard?: () => void;
  /** Width of the time column, matching the transcript it sits in. */
  timeColumnClassName?: string;
}

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
  const lineRef = useRef<HTMLDivElement>(null);
  const name = `at ${timeLabel}, “${noteSnippet(body)}”`;
  return (
    <div
      ref={lineRef}
      // Focus lands here when Retry, which hides its own button, is pressed.
      tabIndex={status === "saved" ? undefined : -1}
      className="flex gap-3 outline-none focus-visible:ring-2 focus-visible:ring-action"
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
          <p className="mt-1 text-xs text-status-danger-fg">{error ?? "Not saved."}</p>
        ) : null}
        {status === "failed" && (onRetry || onDiscard) ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {onRetry ? (
              <Button
                size="sm"
                aria-label={`Retry note ${name}`}
                onClick={() => {
                  lineRef.current?.focus();
                  onRetry();
                }}
                className={NOTE_BUTTON_TOUCH}
              >
                Retry
              </Button>
            ) : null}
            {onDiscard ? (
              <Button
                size="sm"
                aria-label={`Discard note ${name}`}
                onClick={onDiscard}
                className={NOTE_BUTTON_TOUCH}
              >
                Discard
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
