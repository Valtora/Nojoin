"use client";

import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { useEffect, useLayoutEffect, useMemo, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Copy, StickyNote } from "lucide-react";

import type { NoteSelection, OutboxDraft } from "@/lib/notes/noteOutbox";
import type { TranscriptSegment } from "@/types";
import { lineIsSettling } from "@/lib/transcript/settling";
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

const boxOf = (range: Range): Box | null => {
  if (typeof range.getBoundingClientRect !== "function" || range.collapsed) return null;
  if (!range.startContainer.isConnected || !range.endContainer.isConnected) return null;
  const { top, left, bottom, right } = range.getBoundingClientRect();
  return { top, left, bottom, right };
};

/** Where the selected text is on screen, followed through scrolls while it is on the page. */
function useFollowedBox(range: Range, initial: Box) {
  const [box, setBox] = useState(initial);
  useEffect(() => {
    const follow = () => {
      const next = boxOf(range);
      if (next) setBox(next);
    };
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", follow);
    return () => {
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", follow);
    };
  }, [range]);
  return box;
}

/** Where the selection is: what the bar and the note popover sit against. */
function SelectionAnchor({ box, onAnchor }: { box: Box; onAnchor: (anchor: HTMLDivElement | null) => void }) {
  return (
    <div
      ref={onAnchor}
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
  );
}

/** The ⋯ button of the line with `segmentId`, where focus goes after a note on its text. */
const lineTrigger = (root: HTMLElement | null, segmentId: string) => {
  const text = Array.from(root?.querySelectorAll<HTMLElement>("p[data-segment-id]") ?? []).find(
    (paragraph) => paragraph.dataset.segmentId === segmentId,
  );
  return text?.closest("[data-line-actions]")?.querySelector<HTMLElement>('[aria-haspopup="menu"]') ?? null;
};

interface TranscriptSelectionToolbarProps {
  disabledReason?: string;
  captured: CapturedSelection;
  onClose: () => void;
  /** The processed view: starts a note on the selection, from where it is now. */
  onWrite?: (box: Box) => void;
  onNoteOnText?: (selection: NoteSelection) => void;
}

/**
 * Add note and Copy for text selected in a transcript, under the selection,
 * where it doesn't cover a phone's own selection bar above it. It follows the
 * selection when the page scrolls, while the selected text is still on the
 * page.
 */
export function TranscriptSelectionToolbar({
  captured,
  onClose,
  onWrite,
  onNoteOnText,
  disabledReason,
}: TranscriptSelectionToolbarProps) {
  const box = useFollowedBox(captured.range, captured.box);
  const [anchor, setAnchor] = useState<HTMLDivElement | null>(null);
  const anchorRef = useMemo(() => ({ current: anchor }), [anchor]);
  const { panelRef, panelStyle, reposition } = useAnchoredPanel<HTMLDivElement>(true, anchorRef);
  const { selection } = captured;

  // Placed again once the selection's place has moved with a scroll.
  useLayoutEffect(() => {
    reposition();
  }, [box, reposition]);

  const addNote = () => {
    if (!selection || disabledReason) return;
    if (onWrite) {
      onWrite(box);
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
      <SelectionAnchor box={box} onAnchor={setAnchor} />
      <SurfaceBase
        data-ui-fill="card"
        data-ui-border="full" kind="float"
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
            disabled={!!disabledReason}
            title={disabledReason}
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
      </SurfaceBase>
    </>,
    document.body,
  );
}

interface Writing extends CapturedSelection {
  selection: NoteSelection;
}

/**
 * The note popover for a selection, in the processed view. It lives apart
 * from the bar and the selection, which a tap or a scroll drops: once
 * writing starts only the popover's own rule closes it, so a press outside
 * closes it only while its box is empty.
 */
function SelectionNoteWriter({
  writing,
  root,
  onAddNote,
  onClose,
}: {
  writing: Writing;
  root: RefObject<HTMLElement | null>;
  onAddNote: (note: OutboxDraft) => string | null;
  onClose: () => void;
}) {
  const box = useFollowedBox(writing.range, writing.box);
  const [anchor, setAnchor] = useState<HTMLDivElement | null>(null);
  const { selection } = writing;

  return createPortal(
    <>
      <SelectionAnchor box={box} onAnchor={setAnchor} />
      {anchor ? (
        <NoteOnSelectionPopover
          quote={selection.quotedText}
          title={`Note on the selected text at ${formatTimelineMs(selection.startMs)}`}
          anchor={anchor}
          returnFocus={() => lineTrigger(root.current, writing.segmentId)?.focus()}
          onSubmit={(body) => onAddNote({ kind: "selection", body, anchorMs: null, selection })}
          onClose={onClose}
        />
      ) : null}
    </>,
    document.body,
  );
}

/**
 * The selection bar for the transcript lines inside `root`, while some of
 * their text is selected, and the note being written on a selection.
 * Nothing without a way to add a note.
 */
export default function TranscriptSelectionNotes({
  root,
  onAddNote,
  onNoteOnText,
  segments,
}: NoteHandlers & { root: RefObject<HTMLElement | null>; segments?: readonly TranscriptSegment[] }) {
  const [writing, setWriting] = useState<Writing | null>(null);
  const writingNote = writing && onAddNote ? writing : null;
  const { captured, clear } = useCapturedSelection(root, writingNote !== null);

  if (writingNote && onAddNote) {
    return (
      <SelectionNoteWriter
        key={writingNote.seq}
        writing={writingNote}
        root={root}
        onAddNote={onAddNote}
        onClose={() => setWriting(null)}
      />
    );
  }
  if (!captured || (!onAddNote && !onNoteOnText)) return null;
  const { selection } = captured;
  const disabledReason = selection?.sourceUtterances?.some((source) => {
    const segment = segments?.find((line) => line.id === source.id);
    if (!segments) return captured.settling;
    if (!segment) return true;
    if (source.word_ids?.length) {
      return source.word_ids.some((id) => {
        const word = segment.words?.find((word) => word.id === id);
        return !word?.locked || (captured.wordTexts?.[id] !== undefined &&
          word.word.trim() !== captured.wordTexts[id]);
      });
    }
    return lineIsSettling(segment) || segment.revision !== source.revision;
  }) ? "Selected words are still settling or have changed." : undefined;
  return (
    <TranscriptSelectionToolbar
      key={captured.seq}
      captured={captured}
      disabledReason={disabledReason}
      onClose={clear}
      onWrite={
        onAddNote && selection
          ? (box) => {
              setWriting({ ...captured, selection, box });
              clear();
            }
          : undefined
      }
      onNoteOnText={onNoteOnText}
    />
  );
}
