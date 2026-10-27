"use client";

import { useCallback, useEffect, useMemo, useRef, type RefObject } from "react";

import {
  CAPTURE_NOTE_BODY,
  captureShortcutLabel,
  useCaptureActions,
} from "@/lib/captures/useCaptureActions";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import type { AudioClock } from "@/lib/notes/audioClock";
import { segmentStartMs } from "@/lib/notes/placement";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import { Recording, type TimelineNote } from "@/types";

import LiveDocumentsPanel from "./LiveDocumentsPanel";
import MeetingEdgePanel from "./MeetingEdgePanel";
import HeadNotePanel from "./HeadNotePanel";
import LiveNoteComposer from "./notes/LiveNoteComposer";
import RecordingUnsavedNotes from "./notes/RecordingUnsavedNotes";
import { useLiveDocuments } from "./useLiveDocuments";
import type { LiveTranscript } from "./transcript/_hooks/useLiveTranscript";

interface RecordingStatusDisplayProps {
  composerBoxRef?: RefObject<HTMLTextAreaElement | null>;
  recording: Recording;
  liveTranscript: LiveTranscript;
  onSaveMeetingEdgeFocus: (focus: string) => Promise<void>;
  meetingEdgeContextLevel?: number;
  onSaveMeetingEdgeContextLevel?: (level: number) => Promise<void>;
  showMeetingEdge?: boolean;
  /** Where capture is on the audio timeline, for anchoring live notes. */
  audioClock?: AudioClock;
  /**
   * The recording's notes as the page last listed them. Null while they load,
   * which holds the Notes box back: it must not start a head note before it
   * knows whether there is one.
   */
  timelineNotes?: readonly TimelineNote[] | null;
}

const NO_TIMELINE_NOTES: readonly TimelineNote[] = [];
const BODY_NOTES = ["point", "selection"] as const;

export default function RecordingStatusDisplay({
  composerBoxRef: suppliedComposerBoxRef,
  recording,
  liveTranscript,
  onSaveMeetingEdgeFocus,
  meetingEdgeContextLevel,
  onSaveMeetingEdgeContextLevel,
  showMeetingEdge = true,
  audioClock,
  timelineNotes = NO_TIMELINE_NOTES,
}: RecordingStatusDisplayProps) {
  const { documents, refresh: refreshDocuments } = useLiveDocuments(recording.id);
  useEffect(() => {
    const refresh = (event: Event) => {
      if ((event as CustomEvent<{ recordingId: string }>).detail?.recordingId === recording.id) {
        void refreshDocuments();
      }
    };
    window.addEventListener("recording-documents-updated", refresh);
    return () => window.removeEventListener("recording-documents-updated", refresh);
  }, [recording.id, refreshDocuments]);
  const isActiveRecording = isLiveCaptureInProgress(recording);
  // The recording's outbox, which the page keeps (`useRecordingNotes`): a
  // note still on its way, and the box's text, outlive Stop.
  const { outbox, snapshot: outboxState } = useNoteOutbox(recording.id);
  const { entries: outboxEntries } = outboxState;
  const pendingMeetingNotes = useMemo(
    () => outboxEntries.filter((entry) => entry.kind === "head" || entry.kind === "tail"),
    [outboxEntries],
  );
  const liveSegments = liveTranscript.segments;
  // Before the first read of the clock, "now" is past every line heard so
  // far, which is where the clock would place the note anyway.
  const readAudioNow = useCallback(
    () =>
      audioClock?.audioNow(recording.id) ??
      liveSegments.reduce((latest, segment) => Math.max(latest, segmentStartMs(segment)), 0),
    [audioClock, recording.id, liveSegments],
  );
  // Once capture is over, "now" is its end: where the clock stopped, or the
  // recording's length when this page never saw it running.
  const durationSeconds = recording.duration_seconds;
  const readEndMs = useCallback(
    () => audioClock?.audioNow(recording.id) ?? Math.round((durationSeconds ?? 0) * 1000),
    [audioClock, recording.id, durationSeconds],
  );
  // The Capture button and its shortcut while this tab shares a screen: each
  // still is a point note of its own, holding the frame.
  const captureActions = useCaptureActions(
    recording.id,
    readAudioNow,
    useCallback(
      (file: File, atMs: number) =>
        outbox.submit({ kind: "point", body: CAPTURE_NOTE_BODY, anchorMs: atMs }, { file }),
      [outbox],
    ),
  );
  // Where focus goes when a note's Discard removes the button pressed.
  const localComposerBoxRef = useRef<HTMLTextAreaElement>(null);
  const composerBoxRef = suppliedComposerBoxRef ?? localComposerBoxRef;
  const bodyRef = useRef<HTMLElement>(null);
  const focusNoteHome = () => {
    (composerBoxRef.current ?? bodyRef.current)?.focus();
  };
  const notesAreLocked = recording.transcript?.notes_status === "generating" ||
    /generating meeting notes/i.test(recording.processing_step || "");

  return (
    <section ref={bodyRef} tabIndex={-1} aria-label="Live transcript and notes" className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto outline-none">
      {isActiveRecording ? (
        <LiveNoteComposer
          readAudioNow={readAudioNow}
          onSubmit={(note) => outbox.submit(note, { clearDraft: true })}
          draft={outboxState.draft}
          onDraftChange={outbox.setDraft}
          notice={outboxState.notice}
          boxRef={composerBoxRef}
          onCapture={captureActions.canCaptureScreen ? () => void captureActions.captureScreen() : undefined}
          capturing={captureActions.busy}
          captureShortcut={captureShortcutLabel()}
          captureMessage={captureActions.message}
          draftFile={outboxState.draftFile}
          onDraftFileChange={outbox.setDraftFile}
        />
      ) : (
        <RecordingUnsavedNotes
          recordingId={recording.id}
          kinds={BODY_NOTES}
          listUploads
          readEndMs={readEndMs}
          onEmptied={focusNoteHome}
          className="mt-4 border-t border-surface-divider pt-3"
        />
      )}
      {timelineNotes !== null ? (
        <HeadNotePanel
          key={recording.id}
          recordingId={recording.id}
          timelineNotes={timelineNotes}
          pendingNotes={pendingMeetingNotes}
          onRetryPendingNote={outbox.retry}
          onDiscardPendingNote={outbox.discard}
          onPendingNotesEmptied={focusNoteHome}
          // While recording, the note box takes the meeting's notes
          // and the transcript above needs the height.
          compact={isActiveRecording}
          disabled={notesAreLocked}
          disabledMessage="Your manual notes are now being folded into the generated meeting notes. Editing will unlock again once generation finishes."
        />
      ) : null}
      {documents.length > 0 ? <LiveDocumentsPanel documents={documents} /> : null}
      {showMeetingEdge ? (
        <MeetingEdgePanel
          payload={recording.transcript?.meeting_edge_payload}
          focusText={recording.transcript?.meeting_edge_focus}
          status={recording.transcript?.meeting_edge_status}
          onSaveFocus={onSaveMeetingEdgeFocus}
          contextLevel={meetingEdgeContextLevel}
          onSaveContextLevel={onSaveMeetingEdgeContextLevel}
        />
      ) : null}
    </section>
  );
}
