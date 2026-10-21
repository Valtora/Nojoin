"use client";

import type { OutboxEntry } from "@/lib/notes/noteOutbox";
import { useHeadNote } from "@/lib/notes/useHeadNote";
import type { RecordingId, TimelineNote } from "@/types";

import ProcessingNotesPanel from "./ProcessingNotesPanel";

interface HeadNotePanelProps {
  recordingId: RecordingId;
  timelineNotes: readonly TimelineNote[] | null | undefined;
  disabled?: boolean;
  disabledMessage?: string;
  /** Meeting-wide notes from the live note box still on their way. */
  pendingNotes?: readonly OutboxEntry[];
  onRetryPendingNote?: (key: string) => void;
  onDiscardPendingNote?: (key: string) => void;
  onPendingNotesEmptied?: () => void;
  /** A lower floor for the box while capture is live. */
  compact?: boolean;
}

/** The notes box, editing the recording's head note. Key it by recording. */
export default function HeadNotePanel({
  recordingId,
  timelineNotes,
  disabled,
  disabledMessage,
  pendingNotes,
  onRetryPendingNote,
  onDiscardPendingNote,
  onPendingNotesEmptied,
  compact,
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
      pendingNotes={pendingNotes}
      onRetryPendingNote={onRetryPendingNote}
      onDiscardPendingNote={onDiscardPendingNote}
      onPendingNotesEmptied={onPendingNotesEmptied}
      compact={compact}
      retrySaves={clearsEnded}
      disabled={disabled}
      disabledMessage={disabledMessage}
    />
  );
}
