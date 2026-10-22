"use client";

import { useEffect, useLayoutEffect, useMemo, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Copy, StickyNote } from "lucide-react";

import type { NoteSelection, OutboxDraft } from "@/lib/notes/noteOutbox";
import { formatTimelineMs } from "@/lib/notes/placement";

import NoteOnSelectionPopover from "../notes/NoteOnSelectionPopover";
import Button from "../ui/Button";
import { TOUCH_TARGET_HEIGHT } from "../ui/touchTarget";
import { useAnchoredPanel } from "../ui/useAnchoredPanel";

import {
  useCapturedSelection,
  type Box,
  type CapturedSelection,
} from "./_hooks/useCapturedSelection";

interface NoteHandlers {
  /** The processed view: writes the note in a popover here and queues it. */
  onAddNote?: (note: OutboxDraft) => string | null;
  /** The live view: hands the text to the note box, which writes the note. */
  onNoteOnText?: (selection: NoteSelection) => void;
}

interface TranscriptSelectionToolbarProps extends NoteHandlers {
  captured: CapturedSelection;
  onClose: () => void;
}

const boxOf = (range: Range): Box | null => {
  if (typeof range.getBoundingClientRect !== "function" || range.collapsed) return null;
  if (!range.startContainer.isConnected || !range.endContainer.isConnected) return null;
  const { top, left, bottom, right } = range.getBoundingClientRect();
  return { top, left, bottom, right };
};

/**
 * Add note and Copy for text selected in a transcript, under the selection,
 * where it doesn't cover a phone's own selection bar above it. Add note
 * writes the note in a popover in the processed view, and in the note box in
 * the live view. It follows the selection when the page scrolls, while the
 * selected text is still on the page.
 */
export function TranscriptSelectionToolbar({
  captured,
  onClose,
  onAddNote,
  onNoteOnText,
}: TranscriptSelectionToolbarProps) {
  const [box, setBox] = useState(captured.box);
  const [writing, setWriting] = useState(false);
  const [anchor, setAnchor] = useState<HTMLDivElement | null>(null);
  const anchorRef = useMemo(() => ({ current: anchor }), [anchor]);
  const { panelRef, panelStyle, reposition } = useAnchoredPanel<HTMLDivElement>(
    !writing,
    anchorRef,
  );
  const { selection } = captured;

  // Placed again once the selection's place has moved with a scroll.
  useLayoutEffect(() => {
    if (!writing) reposition();
  }, [box, writing, reposition]);

  useEffect(() => {
    const follow = () => {
      const next = boxOf(captured.range);
      if (next) setBox(next);
    };
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", follow);
    return () => {
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", follow);
    };
  }, [captured.range]);

  const addNote = () => {
    if (!selection) return;
    if (onAddNote) {
      setWriting(true);
      return;
    }
    onClose();
    onNoteOnText?.(selection);
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(captured.text);
    } catch {
      // No clipboard API here: the selection is still made, so copy that.
      document.execCommand("copy");
    }
    onClose();
  };

  return createPortal(
    <>
      {/* Where the selection is: what the toolbar and the popover sit against. */}
      <div
        ref={setAnchor}
        aria-hidden="true"
        data-selection-ui
        style={{
          position: "fixed",
          top: box.top,
          left: box.left,
          width: Math.max(0, box.right - box.left),
          height: Math.max(0, box.bottom - box.top),
          pointerEvents: "none",
        }}
      />
      {writing && selection && onAddNote && anchor ? (
        <NoteOnSelectionPopover
          selection={selection}
          title={`Note on the selected text at ${formatTimelineMs(selection.startMs)}`}
          anchor={anchor}
          onSubmit={(body) => onAddNote({ kind: "selection", body, anchorMs: null, selection })}
          onClose={onClose}
        />
      ) : null}
      {!writing ? (
        <div
          ref={panelRef}
          role="toolbar"
          aria-label="Selected text"
          data-selection-ui
          style={panelStyle}
          className="z-[var(--z-dropdown)] flex gap-1 rounded-lg border border-surface-border bg-surface-card p-1 shadow-float"
        >
          {selection ? (
            <Button
              variant="ghost"
              size="sm"
              iconLeft={<StickyNote aria-hidden="true" className="h-4 w-4" />}
              onClick={addNote}
              className={`text-sm ${TOUCH_TARGET_HEIGHT}`}
            >
              Add note
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            iconLeft={<Copy aria-hidden="true" className="h-4 w-4" />}
            onClick={() => void copy()}
            className={`text-sm ${TOUCH_TARGET_HEIGHT}`}
          >
            Copy
          </Button>
        </div>
      ) : null}
    </>,
    document.body,
  );
}

/**
 * The selection toolbar for the transcript lines inside `root`, while some of
 * their text is selected. Nothing without a way to add a note.
 */
export default function TranscriptSelectionNotes({
  root,
  onAddNote,
  onNoteOnText,
}: NoteHandlers & { root: RefObject<HTMLElement | null> }) {
  const { captured, clear } = useCapturedSelection(root);
  if (!captured || (!onAddNote && !onNoteOnText)) return null;
  return (
    <TranscriptSelectionToolbar
      key={captured.seq}
      captured={captured}
      onClose={clear}
      onAddNote={onAddNote}
      onNoteOnText={onNoteOnText}
    />
  );
}
