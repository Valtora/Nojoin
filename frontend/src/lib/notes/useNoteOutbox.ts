"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import type { RecordingId, TimelineNote } from "@/types";

import { NoteOutbox } from "./noteOutbox";

/**
 * The live note box's outbox for one recording (see `NoteOutbox`). Mount it
 * once per recording, above anything that unmounts when capture stops, so a
 * note still saving or not saved outlives the live view.
 */
export function useNoteOutbox(
  recordingId: RecordingId,
  timelineNotes: readonly TimelineNote[] | null | undefined,
) {
  const [outbox] = useState(() => new NoteOutbox(recordingId));

  useEffect(() => {
    outbox.ingest(timelineNotes);
  }, [outbox, timelineNotes]);

  const snapshot = useSyncExternalStore(
    outbox.subscribe,
    outbox.getSnapshot,
    outbox.getSnapshot,
  );

  return {
    ...snapshot,
    submit: outbox.submit,
    retry: outbox.retry,
    discard: outbox.discard,
  };
}
