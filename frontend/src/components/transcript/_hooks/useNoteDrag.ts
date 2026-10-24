"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

import type { MoveTarget } from "@/lib/notes/noteMoves";

/** Where the drop line shows in the scroll region: under the line a drop would follow. */
export interface DropBar {
  top: number;
  left: number;
  width: number;
}

export interface NoteDrag {
  noteId: string;
  /**
   * Where a drop now would move the note: null outside the transcript, over
   * the note's own place or before any line; in the margins between lines,
   * the last target.
   */
  target: MoveTarget | null;
  bar: DropBar | null;
}

interface UseNoteDragOptions {
  /** The transcript's scroll region, which holds the lines. */
  regionRef: RefObject<HTMLElement | null>;
  /** The target in the gap after the shown line at `lineIndex`, if a note can go there. */
  targetAfter: (lineIndex: number) => MoveTarget | null;
  onDrop: (noteId: string, target: MoveTarget) => void;
}

/** How near the region's top or bottom edge a drag scrolls it, and by how much a frame. */
const SCROLL_EDGE_PX = 48;
const SCROLL_STEP_PX = 12;

/** The drop line under a shown line, in the region's scrolled coordinates. */
const barUnder = (region: HTMLElement, lineIndex: number): DropBar | null => {
  const line = region.querySelector<HTMLElement>(`[data-order-index="${lineIndex}"]`);
  if (!line) return null;
  const box = region.getBoundingClientRect();
  const rect = line.getBoundingClientRect();
  return { top: rect.bottom - box.top + region.scrollTop + 4, left: rect.left - box.left, width: rect.width };
};

/**
 * A point note dragged by its grip with a mouse or pen, onto the gap after a
 * line. Over a line, or a note already after it, a drop moves the note after
 * that line. In the margins between lines it keeps the last target. Outside
 * the transcript, over the note's own place, or on Escape, it stays. Near the
 * region's top or bottom the transcript scrolls. Touch is left to scroll the page: on a touch
 * screen the note's Move up and Move down buttons move it.
 */
export function useNoteDrag({ regionRef, targetAfter, onDrop }: UseNoteDragOptions) {
  const [drag, setDrag] = useState<NoteDrag | null>(null);
  const stop = useRef<(() => void) | null>(null);
  useEffect(() => () => stop.current?.(), []);

  const start = (event: ReactPointerEvent<HTMLElement>, noteId: string, shownAfter: number) => {
    const region = regionRef.current;
    if (!region || event.pointerType === "touch" || event.button !== 0) return;
    // No text selection starts under the drag.
    event.preventDefault();
    stop.current?.();
    let point = { x: event.clientX, y: event.clientY };
    let over: MoveTarget | null = null;
    let frame = 0;

    // Redraws only when the target changes, not on every pointer move.
    const aim = (target: MoveTarget | null) => {
      if (target?.afterIndex === over?.afterIndex) return;
      over = target;
      setDrag({ noteId, target, bar: target ? barUnder(region, target.afterIndex) : null });
    };
    const locate = () => {
      const hit = document.elementFromPoint(point.x, point.y);
      if (!hit || !region.contains(hit)) {
        aim(null);
        return;
      }
      // Between the rows, in the margin under a line where the drop line is
      // drawn or a group's time column, the target stays as it was.
      const host = hit.closest<HTMLElement>("[data-order-index], [data-note-gap]");
      if (!host || !region.contains(host)) return;
      const index = Number(host.dataset.orderIndex ?? host.dataset.noteGap);
      aim(index === shownAfter ? null : targetAfter(index));
    };
    const scroll = () => {
      frame = 0;
      const box = region.getBoundingClientRect();
      const step =
        point.y < box.top + SCROLL_EDGE_PX ? -SCROLL_STEP_PX : point.y > box.bottom - SCROLL_EDGE_PX ? SCROLL_STEP_PX : 0;
      if (step === 0) return;
      region.scrollTop += step;
      locate();
      frame = requestAnimationFrame(scroll);
    };
    const onMove = (moved: PointerEvent) => {
      point = { x: moved.clientX, y: moved.clientY };
      locate();
      if (!frame) frame = requestAnimationFrame(scroll);
    };
    const end = (drop: boolean) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("keydown", onKey, true);
      cancelAnimationFrame(frame);
      document.body.style.removeProperty("cursor");
      stop.current = null;
      setDrag(null);
      if (drop && over) onDrop(noteId, over);
    };
    const onUp = () => end(true);
    const onCancel = () => end(false);
    const onKey = (key: KeyboardEvent) => {
      if (key.key !== "Escape") return;
      key.preventDefault();
      key.stopPropagation();
      end(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("keydown", onKey, true);
    document.body.style.cursor = "grabbing";
    stop.current = () => end(false);
    setDrag({ noteId, target: null, bar: null });
  };

  return { drag, start };
}
