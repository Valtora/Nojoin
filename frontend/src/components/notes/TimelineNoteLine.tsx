"use client";

import { useRef, type PointerEvent as ReactPointerEvent } from "react";
import { ChevronDown, ChevronUp, GripVertical, Pencil, StickyNote } from "lucide-react";

import { noteSnippet } from "@/lib/notes/noteOutbox";
import type { NoteAttachment } from "@/types";

import Button from "../ui/Button";
import IconButton from "../ui/IconButton";
import { TOUCH_TARGET_SQUARE } from "../ui/touchTarget";

import { NoteFile, useNoteUpload } from "./NoteFiles";
import { NOTE_BUTTON_TOUCH } from "./noteButtons";

/** Moving a saved point note between transcript lines. */
export interface NoteLineMove {
  /** A step to the gap above or below; null where there is none. */
  onUp: (() => void) | null;
  onDown: (() => void) | null;
  /** Starts a drag from the grip, with a mouse or pen. */
  onGripPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
  dragging: boolean;
  /** Why the last move was refused, shown on the note until the next one. */
  refusal: string | null;
}

interface TimelineNoteLineProps {
  noteId: string;
  body: string;
  /** The words a selection note is on, as they read when it was written. */
  quote?: string | null;
  /**
   * Set when the quote's words are no longer in the transcript near the
   * note's time: the quote is struck through, and `nowReads` is what the
   * transcript says there now (empty when it says nothing).
   */
  wordsChanged?: { nowReads: string } | null;
  /** Opens the note's editor against the button pressed; without it, no Edit button. */
  onEdit?: (trigger: HTMLElement) => void;
  /** The note's time, written the way the surrounding lines write theirs. */
  timeLabel: string;
  /** Whole-meeting notes use their placement instead of an audio time. */
  placementLabel?: string;
  status?: "saved" | "saving" | "failed";
  error?: string | null;
  onRetry?: () => void;
  /** Removes the line: the caller moves focus somewhere that stays. */
  onDiscard?: () => void;
  /** Width of the time column, matching the transcript it sits in. */
  timeColumnClassName?: string;
  /** Move up, Move down and a grip to drag; without it, the note stays put. */
  move?: NoteLineMove;
  /** The note's file, shown under its text; one on its way shows from the outbox. */
  attachment?: NoteAttachment | null;
}

/** The time column of the live view, which a note's own time sits in by default. */
export const NOTE_TIME_COLUMN = "shrink-0 pt-0.5 font-mono text-xs tabular-nums text-contrast-icon-muted";

/**
 * A note in the transcript, on its own line after the line it follows. The
 * same line in the live view and the finished one, so a note reads the same
 * before and after Stop.
 */
export default function TimelineNoteLine({
  noteId,
  body,
  quote = null,
  wordsChanged = null,
  onEdit,
  timeLabel,
  placementLabel,
  status = "saved",
  error = null,
  onRetry,
  onDiscard,
  timeColumnClassName = NOTE_TIME_COLUMN,
  move,
  attachment = null,
}: TimelineNoteLineProps) {
  const lineRef = useRef<HTMLDivElement>(null);
  const { upload } = useNoteUpload(noteId);
  const where = placementLabel?.toLowerCase() ?? `at ${timeLabel}`;
  const name = `${where}, “${noteSnippet(body)}”`;
  return (
    <div
      ref={lineRef}
      // Focus lands here when Retry, which hides its own button, is pressed.
      tabIndex={status === "saved" ? undefined : -1}
      className={`flex flex-wrap outline-none focus-visible:ring-2 focus-visible:ring-action ${
        move?.dragging ? "opacity-50" : ""
      }`}
      data-testid="timeline-note"
      data-note-id={noteId}
      data-note-status={status}
    >
      {/* The time and the note never wrap apart; only the buttons go under them where they don't fit beside. */}
      <div className="flex min-w-0 grow basis-64 gap-3">
        <span className={placementLabel ? "w-24 shrink-0 pt-0.5 text-xs text-contrast-icon-muted" : timeColumnClassName}>
          {placementLabel ?? timeLabel}
        </span>
        <div className="min-w-0 flex-1 border-l-2 border-action-border pl-3">
          <div className="flex flex-wrap items-center gap-x-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-action-text">
            {move ? (
              // A mouse's drag handle; the keyboard and touch have Move up and Move down.
              <span
                aria-hidden="true"
                data-note-grip
                title="Drag to move this note"
                onPointerDown={move.onGripPointerDown}
                className="-my-1 -ml-1 hidden cursor-grab rounded-sm p-1 text-contrast-icon-muted hover:bg-surface-inset hover:text-action-text pointer-fine:inline-flex"
              >
                <GripVertical className="h-3.5 w-3.5" />
              </span>
            ) : null}
            <span className="inline-flex items-center gap-1.5">
              <StickyNote className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="sr-only">Your note {where}:</span>
              <span aria-hidden="true">Note</span>
            </span>
            {status === "saving" ? (
              <span className="font-normal normal-case tracking-normal text-contrast-helper">
                Saving…
              </span>
            ) : null}
          </div>
          {quote ? (
            <blockquote
              data-testid="timeline-note-quote"
              className={`mt-0.5 line-clamp-2 break-words text-sm italic leading-6 text-contrast-helper ${
                wordsChanged ? "line-through" : ""
              }`}
            >
              <span className="sr-only">On the words: </span>“{quote}”
            </blockquote>
          ) : null}
          {quote && wordsChanged ? (
            <p data-testid="timeline-note-words-changed" className="text-xs leading-5 text-contrast-helper">
              Words changed since this note.
              {wordsChanged.nowReads ? ` Here it now reads “${noteSnippet(wordsChanged.nowReads, 120)}”.` : null}
            </p>
          ) : null}
          <p className="mt-0.5 whitespace-pre-wrap break-words text-sm leading-6 text-foreground">
            {body}
          </p>
          {attachment || upload ? <NoteFile noteId={noteId} attachment={attachment} /> : null}
          {move?.refusal ? (
            <p data-testid="timeline-note-move-refused" className="mt-1 text-xs text-status-danger-fg">
              {move.refusal}
            </p>
          ) : null}
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
      {/* Beside the note, or under it where they would leave it too narrow, as on a phone. */}
      {move || (onEdit && status === "saved") ? (
        <div className="ml-auto flex shrink-0 items-start pl-3">
          {move ? (
            <>
              <IconButton
                size="sm"
                data-note-move="up"
                aria-label={`Move note up ${name}`}
                title="Move up a line"
                icon={<ChevronUp aria-hidden="true" />}
                disabled={!move.onUp}
                onClick={() => move.onUp?.()}
                className={`shrink-0 -my-1 ${TOUCH_TARGET_SQUARE}`}
              />
              <IconButton
                size="sm"
                data-note-move="down"
                aria-label={`Move note down ${name}`}
                title="Move down a line"
                icon={<ChevronDown aria-hidden="true" />}
                disabled={!move.onDown}
                onClick={() => move.onDown?.()}
                className={`shrink-0 -my-1 ${TOUCH_TARGET_SQUARE}`}
              />
            </>
          ) : null}
          {onEdit && status === "saved" ? (
            <IconButton
              size="sm"
              aria-label={`Edit note ${name}`}
              title="Edit or delete this note"
              icon={<Pencil aria-hidden="true" />}
              onClick={(event) => onEdit(event.currentTarget)}
              className={`shrink-0 -my-1 ${TOUCH_TARGET_SQUARE}`}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
