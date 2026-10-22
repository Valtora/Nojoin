"use client";

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import type { NoteSelection } from "@/lib/notes/noteOutbox";
import { linePicksOfRange, selectionFromPicks } from "@/lib/notes/selectionRange";

/** How long a selection must hold still before it counts as made. */
export const SELECTION_SETTLE_MS = 250;

/** Where a press doesn't drop a captured selection: the toolbar and what it opens. */
export const SELECTION_UI = "[data-selection-ui]";

export interface Box {
  top: number;
  left: number;
  bottom: number;
  right: number;
}

/** A text selection in a transcript, as it was when it was made. */
export interface CapturedSelection {
  /** Counts captures, so a view can tell a new selection from the one it shows. */
  seq: number;
  /** The note it would make; null when it takes no words with a span. */
  selection: NoteSelection | null;
  /** The selected text as the page has it, for Copy. */
  text: string;
  /** The first line it takes, in page order. */
  segmentId: string;
  /** Where it was on screen, for a toolbar to sit under. */
  box: Box;
  /** The range itself, to place the toolbar again after a scroll while it holds. */
  range: Range;
}

const boxOf = (range: Range): Box => {
  // jsdom has no layout, and no getBoundingClientRect on a Range.
  if (typeof range.getBoundingClientRect !== "function") {
    return { top: 0, left: 0, bottom: 0, right: 0 };
  }
  const { top, left, bottom, right } = range.getBoundingClientRect();
  return { top, left, bottom, right };
};

/**
 * The text selection made in `root`'s transcript lines, captured once it
 * settles: when the mouse button is released, or once it has held still for
 * `SELECTION_SETTLE_MS` (keyboard selection, touch handles).
 *
 * It is captured at once and kept, not read again: the live view rewrites the
 * line in progress on each poll, which can collapse what the page selects.
 * So a selection that collapses keeps what was captured, and a press anywhere
 * but the toolbar, Escape, or a new selection replaces it. A selection a
 * rewrite collapses while it is still being dragged is simply never captured.
 *
 * While `paused` (a note on a selection is being written) it neither
 * captures nor drops anything: presses and new selections are the note's
 * business, not the bar's.
 */
export function useCapturedSelection(root: RefObject<HTMLElement | null>, paused = false) {
  const [captured, setCaptured] = useState<CapturedSelection | null>(null);
  const clear = useCallback(() => setCaptured(null), []);
  const seqRef = useRef(0);

  useEffect(() => {
    if (paused) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pressing = false;

    const capture = () => {
      const container = root.current;
      const selection = document.getSelection();
      if (!container || !selection || selection.rangeCount === 0 || selection.isCollapsed) return;
      const range = selection.getRangeAt(0);
      if (!container.contains(range.commonAncestorContainer)) return;
      const picks = linePicksOfRange(range, container);
      if (picks.length === 0) return;
      seqRef.current += 1;
      setCaptured({
        seq: seqRef.current,
        selection: selectionFromPicks(picks),
        text: selection.toString(),
        segmentId: picks[0].line.segmentId,
        box: boxOf(range),
        range: range.cloneRange(),
      });
    };
    const settle = () => {
      clearTimeout(timer);
      if (!pressing) timer = setTimeout(capture, SELECTION_SETTLE_MS);
    };
    const onPress = (event: PointerEvent) => {
      if ((event.target as Element | null)?.closest?.(SELECTION_UI)) return;
      pressing = true;
      clearTimeout(timer);
      setCaptured(null);
    };
    const onRelease = () => {
      if (!pressing) return;
      pressing = false;
      settle();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) setCaptured(null);
    };

    document.addEventListener("selectionchange", settle);
    document.addEventListener("pointerdown", onPress, true);
    document.addEventListener("pointerup", onRelease, true);
    document.addEventListener("pointercancel", onRelease, true);
    document.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("selectionchange", settle);
      document.removeEventListener("pointerdown", onPress, true);
      document.removeEventListener("pointerup", onRelease, true);
      document.removeEventListener("pointercancel", onRelease, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [root, paused]);

  return { captured, clear };
}
