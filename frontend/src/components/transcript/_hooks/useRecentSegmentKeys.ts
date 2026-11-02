"use client";

import { useMemo, useState, useSyncExternalStore } from "react";
import { NO_LINE_CHANGES, revisedBadgeExpiry, trackLineChanges, type LineChanges } from "../transcriptDisplay";
import { LINE_KEY_SEPARATOR, type IndexedTranscriptSegment } from "../transcriptGroups";

/** After a badge expires, the next expiry waits at least this long, so a burst clears together. */
const EXPIRY_BATCH_MS = 1_000;

const serverSnapshot = () => "";

/**
 * The recent keys of `items` as of the store's own clock, which reads the time
 * once and then only when its timer fires. A snapshot that read the time itself
 * changed between React's reads whenever a badge expired mid-render, and React
 * re-rendered until it gave up: a long transcript written in one burst crashed
 * the page 15 s later (issue #831).
 */
function recentSegmentKeysStore(items: readonly IndexedTranscriptSegment[], changes: LineChanges) {
  let clock: number | null = null;
  let keys = "";
  let lastFired = -Infinity;
  const expiry = (segmentId: string, now: number) => revisedBadgeExpiry(changes.get(segmentId)?.changedAt, now);
  const readClock = (now: number) => {
    clock = now;
    keys = items
      .filter(({ segmentId }) => expiry(segmentId, now) !== null)
      .map(({ segmentId }) => segmentId)
      .join(LINE_KEY_SEPARATOR);
    return now;
  };
  const getSnapshot = () => {
    if (clock === null) readClock(Date.now());
    return keys;
  };
  const subscribe = (onChange: () => void) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const scheduleExpiry = () => {
      const now = clock ?? readClock(Date.now());
      let nextExpiry = Infinity;
      for (const { segmentId } of items) {
        const at = expiry(segmentId, now);
        if (at !== null) nextExpiry = Math.min(nextExpiry, at);
      }
      if (nextExpiry === Infinity) return;
      const at = Math.max(nextExpiry, lastFired + EXPIRY_BATCH_MS);
      timer = setTimeout(() => {
        lastFired = readClock(Date.now());
        scheduleExpiry();
        onChange();
      }, Math.max(0, at - Date.now()));
    };
    scheduleExpiry();
    return () => {
      if (timer !== undefined) clearTimeout(timer);
    };
  };
  return { getSnapshot, subscribe };
}

/**
 * Each line's changes since this view first showed it, kept for the view's
 * life. Recorded during render, as React's docs do for a value derived from
 * the previous render; this render already returns the result.
 */
function useLineChanges(items: readonly IndexedTranscriptSegment[]): LineChanges {
  const [state, setState] = useState(() => ({ items, changes: trackLineChanges(NO_LINE_CHANGES, items) }));
  if (state.items === items) return state.changes;
  const changes = trackLineChanges(state.changes, items);
  setState({ items, changes });
  return changes;
}

/**
 * Visible lines that changed in the last 15 s after this view first showed
 * them, with one timeout for the view's next badge expiry.
 */
export function useRecentSegmentKeys(items: readonly IndexedTranscriptSegment[]): string {
  const changes = useLineChanges(items);
  // A primitive snapshot stays equal between expiries, including under the
  // React Compiler; a clock read hidden in a render helper can be cached.
  const store = useMemo(() => recentSegmentKeysStore(items, changes), [items, changes]);
  return useSyncExternalStore(store.subscribe, store.getSnapshot, serverSnapshot);
}
