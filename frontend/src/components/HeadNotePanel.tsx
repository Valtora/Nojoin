"use client";

import { useHeadNote } from "@/lib/notes/useHeadNote";
import type { RecordingId, TimelineNote } from "@/types";

import ProcessingNotesPanel from "./ProcessingNotesPanel";

interface HeadNotePanelProps {
  recordingId: RecordingId;
  timelineNotes: TimelineNote[] | null | undefined;
  disabled?: boolean;
  disabledMessage?: string;
}

/** The notes box, editing the recording's head note. Key it by recording. */
export default function HeadNotePanel({
  recordingId,
  timelineNotes,
  disabled,
  disabledMessage,
}: HeadNotePanelProps) {
  const {
    body,
    otherNotes,
    conflict,
    clearsEnded,
    saveHeadNote,
    setDirty,
    resolveConflict,
  } = useHeadNote(recordingId, timelineNotes);

  return (
    <ProcessingNotesPanel
      value={body}
      onSave={saveHeadNote}
      onDirtyChange={setDirty}
      conflict={conflict}
      onResolveConflict={resolveConflict}
      otherNotes={otherNotes}
      retrySaves={clearsEnded}
      disabled={disabled}
      disabledMessage={disabledMessage}
    />
  );
}
