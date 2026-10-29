"use client";

import type { Recording, TimelineNote } from "@/types";

import MyNotesList from "./MyNotesList";
import type { ComponentProps } from "react";

type Props = Omit<ComponentProps<typeof MyNotesList>, "recordingId" | "timelineNotes"> & {
  recording: Recording;
  timelineNotes: readonly TimelineNote[] | null;
};

/** One list of the recording's notes; the shared composer stays under Transcript. */
export default function RecordingMyNotes({ recording, timelineNotes, className = "", ...listProps }: Props) {
  return <div className={`flex h-full min-h-0 flex-col overflow-y-auto ${className}`}>
    <MyNotesList {...listProps} recordingId={recording.id} timelineNotes={timelineNotes} className="min-h-0 flex-1" />
  </div>;
}
