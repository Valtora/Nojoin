"use client";

import type { RecordingId } from "@/types";

/**
 * The "this recording no longer exists" announcement.
 *
 * Discard is the one recording action that is both explicit and irreversible,
 * and several surfaces can trigger it. Every list and the open detail page has
 * to drop the meeting at once rather than wait for the rail's 15s poll.
 *
 * `recording-updated` is not enough here. It carries no identity for a deletion
 * and a listener answers anything but a rename with a re-fetch, so the row
 * survives the round trip. This event names the recording, which lets a list drop it from local
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

/**
 * The "this recording changed" announcement. Without a name, every list and
 * the open page read the recording again. With one it is a rename, which each
 * of them applies in place: the name is the whole change, and a list read sent
 * while the rename is still saving could answer with the old name.
 */
export const RECORDING_UPDATED_EVENT = "recording-updated";

export interface RecordingRenamedDetail {
  id: RecordingId;
  name: string;
}

/** Announces that `id` is now called `name`, before or after it has saved. */
export const dispatchRecordingRenamed = (id: RecordingId, name: string) => {
  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<RecordingRenamedDetail>(RECORDING_UPDATED_EVENT, {
      detail: { id, name },
    }),
  );
};

/** The rename an update event carries, or null when it asks for a re-read. */
export const recordingRenameOf = (event: Event): RecordingRenamedDetail | null => {
  const detail = (event as CustomEvent<Partial<RecordingRenamedDetail> | null>).detail;
  return typeof detail?.id === "string" && typeof detail.name === "string" && detail.name !== ""
    ? { id: detail.id, name: detail.name }
    : null;
};

/** `recordings` with one of them renamed: the same array when none changes. */
export const withRecordingName = <T extends { id: RecordingId; name: string }>(
  recordings: T[],
  { id, name }: RecordingRenamedDetail,
): T[] =>
  recordings.some((recording) => recording.id === id && recording.name !== name)
    ? recordings.map((recording) => (recording.id === id ? { ...recording, name } : recording))
    : recordings;
