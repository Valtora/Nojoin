"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import type { RecordingId, TimelineNote } from "@/types";

import { HeadNoteSync } from "./headNoteSync";
import { useHeadNoteOwner } from "./HeadNoteOwner";

const subscribeToNothing = () => () => {};
const noDraft = () => null;

/**
 * The processing notes box's view of the recording's notes (see
 * `HeadNoteSync`). Mount it once per recording: the store is made from the
 * first notes it is given and keeps its state across polls.
 */
export function useHeadNote(
  recordingId: RecordingId,
  timelineNotes: readonly TimelineNote[] | null | undefined,
) {
  const context = useHeadNoteOwner();
  const owner = context?.recordingId === recordingId ? context : null;
  const [localSync] = useState(() => owner ? null : new HeadNoteSync(recordingId, timelineNotes));
  const sync = owner?.sync ?? localSync!;
  const retainedDraft = useSyncExternalStore(
    owner?.subscribeDraft ?? subscribeToNothing,
    owner?.getDraft ?? noDraft,
    owner?.getDraft ?? noDraft,
  );

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
    setDirty: owner?.setDirty ?? sync.setDirty,
    resolveConflict: sync.resolveConflict,
    retainedDraft,
    onDraftTextChange: owner?.setDraftText,
    onDraftSavedChange: owner?.setDraftSaved,
  };
}
