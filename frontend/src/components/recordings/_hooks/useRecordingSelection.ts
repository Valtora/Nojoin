"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { useNavigationStore } from "@/lib/store";
import type { RecordingId } from "@/types";

/**
 * The recordings list's multi-select, the same in Recordings, Archived and
 * Deleted.
 *
 * The selection only ever holds rows that are shown: whenever the list,
 * search, filters or view change, and after any single-row action removes a
 * row, what is no longer shown is dropped, so the batch bar can never act on
 * a row the person cannot see. The Shift anchor is kept by id, so a refresh
 * that moves rows keeps the range starting from the same recording.
 */
export interface RecordingSelection {
  /** The selected rows, in list order. */
  selectedIds: RecordingId[];
  count: number;
  /** Anything selected: a click on a row then toggles it rather than opening it. */
  isSelecting: boolean;
  isSelected: (id: RecordingId) => boolean;
  /** How much of the shown list is selected, for the header checkbox. */
  headerState: "none" | "some" | "all";
  /** The row a range extends from, when it is still shown. */
  anchorId: RecordingId | null;
  /** Toggles one row and makes it the anchor. */
  toggle: (id: RecordingId) => void;
  /** Adds every row from the anchor to this one; with no anchor, toggles it. */
  extendTo: (id: RecordingId) => void;
  /** Selects every shown row, or none once all are selected. */
  toggleAll: () => void;
  clear: () => void;
}

export function useRecordingSelection(
  shownIds: readonly RecordingId[],
): RecordingSelection {
  const {
    selectedRecordingIds,
    selectionAnchorId,
    setRecordingSelection,
    toggleRecordingSelection,
    clearSelection,
    pruneRecordingSelection,
  } = useNavigationStore();

  useEffect(() => {
    pruneRecordingSelection(shownIds);
  }, [shownIds, pruneRecordingSelection]);

  return useMemo<RecordingSelection>(() => {
    // Read through what is shown as well, so a row that has just gone is out
    // of the selection in the same render, before the prune lands.
    const chosen = new Set(selectedRecordingIds);
    const selectedIds = shownIds.filter((id) => chosen.has(id));
    const selected = new Set(selectedIds);
    const count = selectedIds.length;
    const anchorId =
      selectionAnchorId !== null && shownIds.includes(selectionAnchorId)
        ? selectionAnchorId
        : null;

    return {
      selectedIds,
      count,
      isSelecting: count > 0,
      isSelected: (id) => selected.has(id),
      headerState:
        count === 0 ? "none" : count === shownIds.length ? "all" : "some",
      anchorId,
      toggle: toggleRecordingSelection,
      extendTo: (id) => {
        const to = shownIds.indexOf(id);
        if (to < 0) return;
        if (anchorId === null) {
          toggleRecordingSelection(id);
          return;
        }
        const from = shownIds.indexOf(anchorId);
        const range = shownIds.slice(Math.min(from, to), Math.max(from, to) + 1);
        const next = new Set([...selectedIds, ...range]);
        setRecordingSelection(
          shownIds.filter((shown) => next.has(shown)),
          anchorId,
        );
      },
      toggleAll: () => {
        if (count === shownIds.length) {
          clearSelection();
        } else {
          setRecordingSelection([...shownIds], anchorId);
        }
      },
      clear: clearSelection,
    };
  }, [
    shownIds,
    selectedRecordingIds,
    selectionAnchorId,
    setRecordingSelection,
    toggleRecordingSelection,
    clearSelection,
  ]);
}

/** How long a touch must hold still on a row to select it. */
export const LONG_PRESS_MS = 500;
/** How far a touch may drift, in px, before it counts as a scroll. */
const LONG_PRESS_SLOP_PX = 10;

export interface TouchLongPress {
  /** Pointer handlers for one row. Only touch starts a press. */
  bind: (id: RecordingId) => {
    onPointerDown: (event: ReactPointerEvent) => void;
    onPointerMove: (event: ReactPointerEvent) => void;
    onPointerUp: () => void;
    onPointerCancel: () => void;
  };
  /** True once for the click that ends a long press, which must not also act on the row. */
  consumeClick: (id: RecordingId) => boolean;
  /**
   * Android raises contextmenu for a touch held on a link. During a press it
   * completes the long press instead, and the row menu stays shut.
   */
  claimContextMenu: (id: RecordingId) => boolean;
}

/** Long-press on touch, which starts or extends a selection on phones and tablets. */
export function useTouchLongPress(
  onLongPress: (id: RecordingId) => void,
): TouchLongPress {
  const handler = useRef(onLongPress);
  useEffect(() => {
    handler.current = onLongPress;
  });
  const press = useRef<{
    id: RecordingId;
    x: number;
    y: number;
    timer: ReturnType<typeof setTimeout>;
  } | null>(null);
  const fired = useRef<RecordingId | null>(null);

  const cancel = useCallback(() => {
    if (press.current) {
      clearTimeout(press.current.timer);
      press.current = null;
    }
  }, []);
  useEffect(() => cancel, [cancel]);

  const fire = useCallback(
    (id: RecordingId) => {
      cancel();
      fired.current = id;
      handler.current(id);
    },
    [cancel],
  );

  return useMemo<TouchLongPress>(
    () => ({
      bind: (id) => ({
        onPointerDown: (event) => {
          fired.current = null;
          cancel();
          if (event.pointerType !== "touch") return;
          press.current = {
            id,
            x: event.clientX,
            y: event.clientY,
            timer: setTimeout(() => fire(id), LONG_PRESS_MS),
          };
        },
        onPointerMove: (event) => {
          const current = press.current;
          if (
            current &&
            Math.hypot(event.clientX - current.x, event.clientY - current.y) >
              LONG_PRESS_SLOP_PX
          ) {
            cancel();
          }
        },
        onPointerUp: cancel,
        onPointerCancel: cancel,
      }),
      consumeClick: (id) => {
        if (fired.current !== id) return false;
        fired.current = null;
        return true;
      },
      claimContextMenu: (id) => {
        if (fired.current === id) return true;
        if (press.current?.id !== id) return false;
        fire(id);
        return true;
      },
    }),
    [cancel, fire],
  );
}
