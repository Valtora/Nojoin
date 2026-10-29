"use client";

import { ButtonBase } from '@/components/ui/nativeBases';
import { type ReactNode } from "react";
import { ChevronDown, StickyNote } from "lucide-react";

import { cn } from "@/lib/cn";

import { TOUCH_TARGET_HEIGHT } from "../ui/touchTarget";

import { NOTE_TIME_COLUMN } from "./TimelineNoteLine";

interface CollapsedLineNotesProps<Note extends { id: string }> {
  /** The notes after one line, in the order they show. */
  notes: readonly Note[];
  /** Notes needing recovery or holding an editor stay visible. */
  isFoldable: (note: Note) => boolean;
  open: boolean;
  onToggle: () => void;
  gapIndex: number;
  saving: boolean;
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
 * Saved and sending notes fold immediately, while failures and active editors
 * remain visible. The reader owns expansion by line identity, so regrouping
 * overlapping transcript lines does not discard an explicit choice.
 */
export default function CollapsedLineNotes<Note extends { id: string }>({
  notes,
  isFoldable,
  open,
  onToggle,
  gapIndex,
  saving,
  renderNote,
  lineTime,
  as: Row = "div",
  timeColumnClassName = NOTE_TIME_COLUMN,
  className,
}: CollapsedLineNotesProps<Note>) {
  const folded = notes.filter(isFoldable).length;
  // A single note shows as it is: a chip in its place would save no room.
  if (folded < 2) return <>{notes.map(renderNote)}</>;

  const count = `${folded} notes`;
  const time = lineTime();
  return (
    <>
      <Row className={cn("flex gap-3", className)} data-testid="line-notes-chip" data-note-gap={gapIndex}>
        {/* The line's time, unseen: the column is as wide as the notes' own. */}
        <span className={cn(timeColumnClassName, "invisible")} aria-hidden="true">
          {time}
        </span>
        <div className="min-w-0 flex-1 border-l-2 border-action-border pl-3">
          <ButtonBase
            type="button"
            aria-expanded={open}
            aria-label={`${count} on the line at ${time}${saving ? ", saving" : ""}`}
            onClick={onToggle}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-pill px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-action-text outline-none hover:text-action-text-hover focus-visible:ring-2 focus-visible:ring-action",
              TOUCH_TARGET_HEIGHT,
            )}
          >
            <StickyNote className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{count}</span>
            {saving && <span className="font-normal normal-case tracking-normal">Saving…</span>}
            <ChevronDown
              className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")}
              aria-hidden="true"
            />
          </ButtonBase>
        </div>
      </Row>
      {notes.filter((note) => open || !isFoldable(note)).map(renderNote)}
    </>
  );
}
