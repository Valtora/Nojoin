"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { stripNote } from "@/lib/notes/headNoteSync";
import type { NoteSelection } from "@/lib/notes/noteOutbox";
import { formatTimelineMs } from "@/lib/notes/placement";

import Button from "../ui/Button";
import { useAnchoredPanel } from "../ui/useAnchoredPanel";

import { NOTE_BUTTON_TOUCH } from "./noteButtons";

interface NoteOnSelectionPopoverProps {
  selection: NoteSelection;
  /**
   * What it sits against: the ⋯ button of the line, which stays mounted
   * while it is open. Focus goes back there when it closes.
   */
  anchor: HTMLElement;
  /** Queues the note; returns why it was refused, or null once it is on its way. */
  onSubmit: (body: string) => string | null;
  onClose: () => void;
}

/**
 * A note on chosen transcript text, in the finished view: the quote, a box
 * and Save. Enter saves and Shift+Enter starts a new line, as in the live
 * note box; Escape and Cancel close it. A press outside closes it only while
 * the box is empty, so a stray tap never throws a note away.
 */
export default function NoteOnSelectionPopover({
  selection,
  anchor,
  onSubmit,
  onClose,
}: NoteOnSelectionPopoverProps) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const anchorRef = useMemo(() => ({ current: anchor }), [anchor]);
  const { panelRef, panelStyle } = useAnchoredPanel<HTMLDivElement>(true, anchorRef);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const placed = panelStyle.visibility === "visible";
  const titleId = useId();
  const errorId = useId();
  const isBlank = stripNote(text) === "";
  const where = formatTimelineMs(selection.startMs);

  const close = () => {
    onClose();
    anchor.focus();
  };

  // The box takes focus once the popover is placed: before that it is
  // hidden, and a hidden box can't be focused.
  useEffect(() => {
    if (placed) boxRef.current?.focus();
  }, [placed]);

  useEffect(() => {
    if (!isBlank) return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || anchor.contains(target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", closeOnOutsidePress, true);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress, true);
  }, [isBlank, anchor, onClose, panelRef]);

  const save = () => {
    if (isBlank) return;
    const refusal = onSubmit(text);
    if (refusal) {
      setError(refusal);
      return;
    }
    close();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      save();
    }
  };

  // On the body, so no translucent transcript group (a stacking context)
  // keeps it under the lines after it.
  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-labelledby={titleId}
      style={panelStyle}
      data-testid="note-on-selection"
      className="z-[var(--z-dropdown)] w-80 max-w-[calc(100vw-1rem)] rounded-lg border border-surface-border bg-surface-card p-3 shadow-float"
      onClick={(event) => event.stopPropagation()}
    >
      <p id={titleId} className="text-xs font-semibold text-contrast-helper">
        Note on the line at {where}
      </p>
      <blockquote className="mt-1 line-clamp-3 border-l-2 border-action-border pl-2 text-sm italic leading-5 text-contrast-muted">
        {selection.quotedText}
      </blockquote>
      <textarea
        ref={boxRef}
        rows={3}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setError(null);
        }}
        onKeyDown={handleKeyDown}
        aria-label="Note"
        aria-describedby={error ? errorId : undefined}
        aria-invalid={error ? true : undefined}
        placeholder="Add a note…"
        className="density-surface-panel mt-2 w-full resize-none border border-control-border bg-control-bg px-3 py-2 text-sm leading-5 text-contrast-muted outline-none focus:border-action focus:ring-2 focus:ring-action"
      />
      {error ? (
        <p id={errorId} className="mt-1 text-xs text-status-danger-fg">
          {error}
        </p>
      ) : null}
      <div className="mt-2 flex justify-end gap-2">
        <Button size="sm" onClick={close} className={NOTE_BUTTON_TOUCH}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={isBlank}
          onClick={save}
          className={NOTE_BUTTON_TOUCH}
        >
          Save note
        </Button>
      </div>
    </div>,
    document.body,
  );
}
