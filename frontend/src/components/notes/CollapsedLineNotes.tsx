"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown, StickyNote } from "lucide-react";

import { cn } from "@/lib/cn";

import { TOUCH_TARGET_HEIGHT } from "../ui/touchTarget";

import { NOTE_TIME_COLUMN } from "./TimelineNoteLine";

interface CollapsedLineNotesProps<Note extends { id: string }> {
  /** The notes after one line, in the order they show. */
  notes: readonly Note[];
  /** Saved notes fold behind the chip; any other always shows in full. */
  isSaved: (note: Note) => boolean;
  renderNote: (note: Note) => ReactNode;
  /**
   * The line's time, for the chip's name: "2 notes on the line at 01:05".
   * Read only for a chip, so a line without notes formats nothing.
   */
  lineTime: () => string;
  /** The element the chip's row is: an `li` among the live view's lines. */
  as?: "div" | "li";
  /** Width of the time column, matching the transcript it sits in. */
  timeColumnClassName?: string;
  className?: string;
}

/**
 * The notes after a transcript line, folded behind a chip with their count
 * ("2 notes") that shows them on a click, a tap or Enter.
 *
 * Only notes that arrive saved fold. One still sending, refused, or kept to
 * retry always shows in full, so nothing unsaved hides behind the chip; and
 * one written here stays in full once it saves, rather than folding away from
 * under the person who just wrote it.
 */
export default function CollapsedLineNotes<Note extends { id: string }>({
  notes,
  isSaved,
  renderNote,
  lineTime,
  as: Row = "div",
  timeColumnClassName = NOTE_TIME_COLUMN,
  className,
}: CollapsedLineNotesProps<Note>) {
  const [open, setOpen] = useState(false);
  const [writtenHere, setWrittenHere] = useState<ReadonlySet<string>>(() => new Set());

  // Noted while rendering, as React suggests for state that follows props:
  // a note seen unsaved is kept here for as long as the chip is mounted.
  const newlyUnsaved = notes.filter((note) => !isSaved(note) && !writtenHere.has(note.id));
  if (newlyUnsaved.length > 0) {
    setWrittenHere(new Set([...writtenHere, ...newlyUnsaved.map((note) => note.id)]));
  }

  const folds = (note: Note) => isSaved(note) && !writtenHere.has(note.id);
  const folded = notes.filter(folds).length;
  if (folded === 0) return <>{notes.map(renderNote)}</>;

  const count = folded === 1 ? "1 note" : `${folded} notes`;
  const time = lineTime();
  return (
    <>
      <Row className={cn("flex gap-3", className)} data-testid="line-notes-chip">
        {/* The line's time, unseen: the column is as wide as the notes' own. */}
        <span className={cn(timeColumnClassName, "invisible")} aria-hidden="true">
          {time}
        </span>
        <div className="min-w-0 flex-1 border-l-2 border-action-border pl-3">
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${count} on the line at ${time}`}
            onClick={() => setOpen((wasOpen) => !wasOpen)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-pill px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-action-text outline-none hover:text-action-text-hover focus-visible:ring-2 focus-visible:ring-action",
              TOUCH_TARGET_HEIGHT,
            )}
          >
            <StickyNote className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{count}</span>
            <ChevronDown
              className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")}
              aria-hidden="true"
            />
          </button>
        </div>
      </Row>
      {notes.filter((note) => open || !folds(note)).map(renderNote)}
    </>
  );
}
