"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";

import {
  CAPTURE_NOTE_BODY,
  captureShortcutLabel,
  useCaptureActions,
} from "@/lib/captures/useCaptureActions";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import type { AudioClock } from "@/lib/notes/audioClock";
import { inlineNotes, isNoteSelection, type NoteTarget } from "@/lib/notes/noteOutbox";
import { segmentStartMs } from "@/lib/notes/placement";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import type { NoteEdits } from "@/lib/notes/useNoteEdits";
import { ClientStatus, Recording, RecordingStatus, type TimelineNote } from "@/types";

import LiveDocumentsPanel from "./LiveDocumentsPanel";
import LiveTranscriptPanel from "./LiveTranscriptPanel";
import MeetingEdgePanel from "./MeetingEdgePanel";
import HeadNotePanel from "./HeadNotePanel";
import LiveNoteComposer from "./notes/LiveNoteComposer";
import RecordingUnsavedNotes from "./notes/RecordingUnsavedNotes";
import { useRecordingActions } from "./recordings/_hooks/useRecordingActions";
import { useLiveDocuments } from "./useLiveDocuments";
import { useLiveSpeakerNaming } from "./transcript/_hooks/useLiveSpeakerNaming";
import type { LiveTranscript } from "./transcript/_hooks/useLiveTranscript";

interface RecordingStatusDisplayProps {
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
  noteEdits?: NoteEdits;
}

const NO_TIMELINE_NOTES: readonly TimelineNote[] = [];
const BODY_NOTES = ["point", "selection"] as const;

export default function RecordingStatusDisplay({
  recording,
  liveTranscript,
  onSaveMeetingEdgeFocus,
  meetingEdgeContextLevel,
  onSaveMeetingEdgeContextLevel,
  showMeetingEdge = true,
  audioClock,
  timelineNotes = NO_TIMELINE_NOTES,
  noteEdits,
}: RecordingStatusDisplayProps) {
  const actions = useRecordingActions();
  const liveNaming = useLiveSpeakerNaming(
    recording.id,
    liveTranscript.applyLiveSpeakerNames,
  );
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
  const { entries: outboxEntries, saved: outboxSaved, discarded: outboxDiscarded } =
    outboxState;
  const liveNotes = useMemo(
    () => {
      const notes = inlineNotes(timelineNotes, {
        entries: outboxEntries,
        saved: outboxSaved,
        discarded: outboxDiscarded,
      });
      const placed = noteEdits && liveTranscript.segments.some((segment) => segment.settling_required)
        ? noteEdits.apply(notes) : notes;
      // Stopped notes keep their existing recovery controls, without duplicates among lines.
      return isActiveRecording ? placed : placed.filter((note) => note.status === "saved");
    },
    [timelineNotes, outboxEntries, outboxSaved, outboxDiscarded, noteEdits, liveTranscript.segments, isActiveRecording],
  );
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
  const composerBoxRef = useRef<HTMLTextAreaElement>(null);
  const bodyRef = useRef<HTMLElement>(null);
  const focusNoteHome = useCallback(() => {
    (composerBoxRef.current ?? bodyRef.current)?.focus();
  }, []);
  const discardInlineNote = useCallback(
    (key: string) => {
      outbox.discard(key);
      composerBoxRef.current?.focus();
    },
    [outbox],
  );
  // "Note on this line": the box's next note goes on the line, and the box
  // takes focus to write it.
  const noteOnLine = useCallback(
    (target: NoteTarget) => {
      outbox.setDraft({ ...outbox.getSnapshot().draft,
        selection: isNoteSelection(target) ? target : null,
        lineSpan: isNoteSelection(target) ? null : target });
      composerBoxRef.current?.focus();
    },
    [outbox],
  );

  const isPaused = recording.status === RecordingStatus.PAUSED || recording.client_status === ClientStatus.PAUSED;
  const notesAreLocked = recording.transcript?.notes_status === "generating" ||
    /generating meeting notes/i.test(recording.processing_step || "");

  return (
    <section ref={bodyRef} tabIndex={-1} aria-label="Live transcript and notes" className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto outline-none">
      <div className="flex min-h-[24rem] flex-1 flex-col">
        <LiveTranscriptPanel
          segments={liveTranscript.segments}
          hasLoaded={liveTranscript.hasLoaded}
          speakers={liveTranscript.speakers}
          lineHandlers={{
            editText: async (segment, text) => {
              const saved = await actions.editTranscriptLine(recording.id, segment, text);
              if (saved) liveTranscript.refresh();
              return saved;
            },
            changeSpeaker: async (segment, assignment) => {
              const saved = await actions.assignTranscriptLine(recording.id, segment, assignment);
              if (saved) liveTranscript.refresh();
              return saved;
            },
            split: async (segment, wordId) => {
              const saved = await actions.splitTranscriptLine(recording.id, segment, wordId);
              if (saved) liveTranscript.refresh();
              return saved;
            },
          }}
          isPaused={isPaused}
          notes={liveNotes}
          onMoveNote={liveSegments.some((segment) => segment.settling_required) ? noteEdits?.move : undefined}
          onRetryNote={outbox.retry}
          onDiscardNote={discardInlineNote}
          onNoteOnLine={isActiveRecording ? noteOnLine : undefined}
          liveSpeakerNames={liveTranscript.liveSpeakerNames}
          onRenameLiveSpeaker={isActiveRecording ? liveNaming.renameLiveSpeaker : undefined}
          loadPeopleNames={liveNaming.loadPeopleNames}
          footer={isActiveRecording ? (
            <LiveNoteComposer
              readAudioNow={readAudioNow}
              onSubmit={(note) => outbox.submit(note, { clearDraft: true })}
              draft={outboxState.draft}
              onDraftChange={outbox.setDraft}
              notice={outboxState.notice}
              boxRef={composerBoxRef}
              onCapture={
                captureActions.canCaptureScreen
                  ? () => void captureActions.captureScreen()
                  : undefined
              }
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
        />
      </div>
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
