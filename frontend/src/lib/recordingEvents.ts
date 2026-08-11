"use client";

import type { RecordingId } from "@/types";

/**
 * The "this recording no longer exists" announcement.
 *
 * Discard is the one recording action that is both explicit and irreversible,
 * and it can be triggered from five places: the recordings rail, the live
 * transport controls, the floating badge, the resume-or-discard modal, and the
 * processing view. Only the rail used to update itself afterwards, so a discard
 * from any of the other four left the meeting on screen until the rail's 15s
 * poll came round.
 *
 * `recording-updated` is not enough here. It carries no identity for a deletion
 * and every listener answers it with a re-fetch, so the row survives the round
 * trip. This event names the recording, which lets a list drop it from local
 * state immediately and the open detail page navigate away from a meeting that
 * has just been deleted underneath it.
 */
export const RECORDING_REMOVED_EVENT = "recording-removed";

export interface RecordingRemovedDetail {
  id: RecordingId;
}

/** Announces that `id` has been deleted server-side and should disappear now. */
export const dispatchRecordingRemoved = (id: RecordingId) => {
  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<RecordingRemovedDetail>(RECORDING_REMOVED_EVENT, {
      detail: { id },
    }),
  );
};

/**
 * Subscribes to removals. Returns the unsubscribe function so a `useEffect`
 * can return it directly.
 */
export const subscribeRecordingRemoved = (
  handler: (id: RecordingId) => void,
): (() => void) => {
  if (typeof window === "undefined") {
    return () => {};
  }

  const listener = (event: Event) => {
    const detail = (event as CustomEvent<RecordingRemovedDetail>).detail;
    if (detail?.id) {
      handler(detail.id);
    }
  };

  window.addEventListener(RECORDING_REMOVED_EVENT, listener);
  return () => window.removeEventListener(RECORDING_REMOVED_EVENT, listener);
};
