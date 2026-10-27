"use client";

import HeadNotePanel from "@/components/HeadNotePanel";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import type { Recording, TimelineNote } from "@/types";

import MyNotesList from "./MyNotesList";
import type { ComponentProps } from "react";

type Props = Omit<ComponentProps<typeof MyNotesList>, "recordingId" | "timelineNotes"> & {
  recording: Recording;
  timelineNotes: readonly TimelineNote[] | null;
};

/** The autosaving meeting note stays above the recording's note list. */
export default function RecordingMyNotes({ recording, timelineNotes, className = "", ...listProps }: Props) {
  const { outbox, snapshot } = useNoteOutbox(recording.id);
  const locked = recording.transcript?.notes_status === "generating" ||
    /generating meeting notes/i.test(recording.processing_step || "");
  return <div className={`flex h-full min-h-0 flex-col overflow-y-auto ${className}`}>
    {timelineNotes !== null ? <div className="shrink-0 p-3">
      <HeadNotePanel key={recording.id}
        recordingId={recording.id}
        timelineNotes={timelineNotes}
        pendingNotes={snapshot.entries.filter((entry) => entry.kind === "head" || entry.kind === "tail")}
        onRetryPendingNote={outbox.retry}
        onDiscardPendingNote={outbox.discard}
        compact={isLiveCaptureInProgress(recording)}
        disabled={locked}
        disabledMessage="Your manual notes are now being folded into the generated meeting notes. Editing will unlock again once generation finishes."
      />
    </div> : null}
    <MyNotesList {...listProps} recordingId={recording.id} timelineNotes={timelineNotes} className="min-h-0 flex-1" />
  </div>;
}
