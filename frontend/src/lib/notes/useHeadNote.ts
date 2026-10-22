"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import type { RecordingId, TimelineNote } from "@/types";

import { HeadNoteSync } from "./headNoteSync";

/**
 * The processing notes box's view of the recording's notes (see
 * `HeadNoteSync`). Mount it once per recording: the store is made from the
 * first notes it is given and keeps its state across polls.
 */
export function useHeadNote(
  recordingId: RecordingId,
  timelineNotes: readonly TimelineNote[] | null | undefined,
) {
  const [sync] = useState(() => new HeadNoteSync(recordingId, timelineNotes));

  useEffect(() => {
    sync.ingest(timelineNotes);
  }, [sync, timelineNotes]);

  const snapshot = useSyncExternalStore(
    sync.subscribe,
    sync.getSnapshot,
    sync.getSnapshot,
  );

  return {
    ...snapshot,
    saveHeadNote: sync.save,
    setDirty: sync.setDirty,
    resolveConflict: sync.resolveConflict,
  };
}
