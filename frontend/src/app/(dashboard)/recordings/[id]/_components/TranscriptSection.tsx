"use client";

import { useCallback, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { AlertCircle } from "lucide-react";

import TranscriptView from "@/components/TranscriptView";
import type { LiveTranscript } from "@/components/transcript/_hooks/useLiveTranscript";
import type { LiveTranscriptPresentation } from "@/components/transcript/liveTranscriptPresentation";
import type { LiveLineHandlers } from "@/components/transcript/LiveLineEditor";
import { useLiveSpeakerNaming } from "@/components/transcript/_hooks/useLiveSpeakerNaming";
import { useStableCallback } from "@/components/transcript/_hooks/useStableCallback";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import { isNoteSelection, type NoteTarget } from "@/lib/notes/noteOutbox";
import { ClientStatus } from "@/types";
import RecordingUnsavedNotes from "@/components/notes/RecordingUnsavedNotes";
import { inlineNotes, type OutboxDraft } from "@/lib/notes/noteOutbox";
import type { EditableNote, NoteEdits } from "@/lib/notes/useNoteEdits";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import type { SpeakerNameDecision } from "@/lib/speakerNameReview";
import { useSpeakerNameReview } from "@/lib/useSpeakerNameReview";
import { useNotificationStore } from "@/lib/notificationStore";
import { transcriptLineActions } from "@/components/recordings/_hooks/transcriptLineActions";
import { isRecordingInFlight } from "../_hooks/recordingDetailUtils";
import {
  RecordingStatus,
  type GlobalSpeaker,
  type Recording,
  type RecordingSpeaker,
  type TimelineNote,
  type TranscriptSegment,
  type TranscriptSpeakerAssignment,
} from "@/types";

// One empty list for every render, so the transcript's memoised lines hold.
const NO_SPEAKERS: readonly RecordingSpeaker[] = Object.freeze([]);
const IGNORE_LIVE_NAMES = () => {};
/** Unsaved notes shown on their line; the rest are listed above the transcript. */
const SELECTION_NOTES = ["selection"] as const;
const LISTED_NOTES = ["point", "head", "tail"] as const;

interface TranscriptSectionProps {
  liveTranscript?: LiveTranscript;
  liveNoteBoxRef?: RefObject<HTMLTextAreaElement | null>;
  active: boolean;
  recording: Recording;
  inFlightContent?: ReactNode;
  canPlayback?: boolean;
  canExport?: boolean;
  transcriptIsAuthoritative?: boolean;
  /** The recording's notes as last listed; null while they load. */
  timelineNotes?: readonly TimelineNote[] | null;
  /** The page's note edits, which My notes shares. */
  noteEdits: NoteEdits;
  transcriptSegments: TranscriptSegment[];
  currentTime: number;
  isPlaying: boolean;
  speakerMap: Record<string, string>;
  speakerColors: Record<string, string>;
  globalSpeakers: GlobalSpeaker[];
  canUndo: boolean;
  canRedo: boolean;
  deferredTranscriptUtteranceIds: string[];
  onPlaySegment: (start: number, end?: number) => void | Promise<void>;
  onPause: () => void;
  onResume: () => void;
  onRenameSpeaker: (label: string, newName: string) => void | boolean | Promise<void | boolean>;
  /** Reloads the recording, after a line confirms or dismisses a suggested name. */
  onRefresh: () => void;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | boolean | Promise<void | boolean>;
  onUpdateSegmentText: (
    segment: TranscriptSegment,
    text: string,
  ) => void | boolean | Promise<void | boolean>;
  onFindAndReplace: (
    find: string,
    replace: string,
    options?: { caseSensitive?: boolean; useRegex?: boolean },
  ) => void | Promise<void>;
  onUndo: () => void;
  onRedo: () => void;
  onExport: () => void;
  onActiveEditUtteranceChange: (id: string | null) => void;
}

export default function TranscriptSection({
  liveTranscript,
  liveNoteBoxRef,
  active,
  recording,
  inFlightContent,
  canPlayback = true,
  canExport = true,
  transcriptIsAuthoritative = false,
  timelineNotes,
  noteEdits,
  transcriptSegments,
  currentTime,
  isPlaying,
  speakerMap,
  speakerColors,
  globalSpeakers,
  canUndo,
  canRedo,
  deferredTranscriptUtteranceIds,
  onPlaySegment,
  onPause,
  onResume,
  onRenameSpeaker,
  onRefresh,
  onUpdateSegmentSpeaker,
  onUpdateSegmentText,
  onFindAndReplace,
  onUndo,
  onRedo,
  onExport,
  onActiveEditUtteranceChange,
}: TranscriptSectionProps) {
  const { addNotification } = useNotificationStore();
  const usesLiveFeed = liveTranscript !== undefined;
  const [hasShownLive, setHasShownLive] = useState(usesLiveFeed);
  useOnDepsChange([usesLiveFeed], () => {
    if (usesLiveFeed) setHasShownLive(true);
  });
  const lineActions = useMemo(() => transcriptLineActions((message) =>
    addNotification({ type: "error", message })), [addNotification]);
  const applyLiveNames = liveTranscript?.applyLiveSpeakerNames ?? IGNORE_LIVE_NAMES;
  const liveNaming = useLiveSpeakerNaming(recording.id, applyLiveNames);
  const activeCapture = isLiveCaptureInProgress(recording);
  const readOnly = isRecordingInFlight(recording) && !activeCapture;
  // A replacement reader must not remount a finished recording's drafts.
  // Capture keeps its live presentation through processing until handoff.
  const [presentFeed, setPresentFeed] = useState(usesLiveFeed);
  useOnDepsChange([usesLiveFeed, readOnly], () => {
    if (!usesLiveFeed) setPresentFeed(false);
    else if (!readOnly) setPresentFeed(true);
  });
  // Editors retain their mutation and revision, but capability and refresh
  // follow the selected reader, including after an awaited write completes.
  const canWrite = useStableCallback(() => !readOnly);
  const refreshSelectedReader = useStableCallback(liveTranscript?.refresh ?? onRefresh);
  const splitLine = async (segment: TranscriptSegment, wordId: string) => {
    if (!canWrite()) return false;
    const saved = await lineActions.splitTranscriptLine(recording.id, segment, wordId);
    if (saved) refreshSelectedReader();
    return saved;
  };
  const nameReview = useSpeakerNameReview({ recordingId: recording.id, onRefresh });
  const reviewSpeakerName = (label: string, name: string, decision: SpeakerNameDecision) =>
    nameReview.review({ labels: [label], name }, decision);
  const transcriptStatus = recording.transcript?.transcript_status;
  // The listed notes and those saved from this page since, less any it
  // discarded: the outbox outlives the live view, and its notes land here.
  // A note added here from a line's menu shows on its line while it saves;
  // the others still on their way are listed above the transcript.
  const { outbox, snapshot: outboxState } = useNoteOutbox(recording.id);
  const {
    entries: outboxEntries,
    saved: outboxSaved,
    discarded: outboxDiscarded,
  } = outboxState;
  // Edits, moves and deletes made here or in My notes show at once: a finished
  // recording's list is not fetched again.
  const { apply: applyEdits, edit: editNote, remove: removeNote, move: moveNote } = noteEdits;
  const notes = useMemo(
    () =>
      applyEdits(
        inlineNotes(
          timelineNotes,
          { entries: outboxEntries, saved: outboxSaved, discarded: outboxDiscarded },
          usesLiveFeed ? undefined : SELECTION_NOTES,
        ),
      ),
    [applyEdits, timelineNotes, outboxEntries, outboxSaved, outboxDiscarded, usesLiveFeed],
  );
  const sectionRef = useRef<HTMLDivElement>(null);
  const durationSeconds = recording.duration_seconds;
  const readEndMs = useCallback(
    () => Math.round((durationSeconds ?? 0) * 1000),
    [durationSeconds],
  );
  const focusSection = useCallback(() => sectionRef.current?.focus(), []);
  const addNote = useCallback((note: OutboxDraft) => outbox.submit(note), [outbox]);
  const noteOnLine = useCallback((target: NoteTarget) => {
    outbox.setDraft({ ...outbox.getSnapshot().draft,
      selection: isNoteSelection(target) ? target : null,
      lineSpan: isNoteSelection(target) ? null : target });
    liveNoteBoxRef?.current?.focus();
  }, [outbox, liveNoteBoxRef]);
  const recordingId = recording.id;
  const liveLineHandlers = useMemo<LiveLineHandlers>(() => ({
    editText: async (segment, text) => {
      if (!canWrite()) return false;
      const saved = await lineActions.editTranscriptLine(recordingId, segment, text);
      if (saved) refreshSelectedReader();
      return saved;
    },
    changeSpeaker: async (segment, assignment) => {
      if (!canWrite()) return false;
      const saved = await lineActions.assignTranscriptLine(recordingId, segment, assignment);
      if (saved) refreshSelectedReader();
      return saved;
    },
    split: async (segment, wordId) => {
      if (!canWrite()) return false;
      const saved = await lineActions.splitTranscriptLine(recordingId, segment, wordId);
      if (saved) refreshSelectedReader();
      return saved;
    },
  }), [recordingId, refreshSelectedReader, canWrite, lineActions]);
  const paused = recording.status === RecordingStatus.PAUSED || recording.client_status === ClientStatus.PAUSED;
  const hasLoaded = liveTranscript?.hasLoaded ?? false;
  const live = useMemo<LiveTranscriptPresentation | undefined>(() => usesLiveFeed && presentFeed ? ({
    hasLoaded, isPaused: paused, lineHandlers: liveLineHandlers,
    onNoteOnLine: activeCapture ? noteOnLine : undefined,
    onRenameLiveSpeaker: activeCapture ? liveNaming.renameLiveSpeaker : undefined,
    loadPeopleNames: liveNaming.loadPeopleNames,
  }) : undefined, [usesLiveFeed, presentFeed, hasLoaded, paused, liveLineHandlers, activeCapture,
    noteOnLine, liveNaming.renameLiveSpeaker, liveNaming.loadPeopleNames]);
  const selectedSpeakerMap = useMemo(() => ({ ...speakerMap, ...liveTranscript?.liveSpeakerNames }),
    [speakerMap, liveTranscript?.liveSpeakerNames]);
  const discardNote = useCallback(
    (key: string) => {
      outbox.discard(key);
      if (liveNoteBoxRef?.current) liveNoteBoxRef.current.focus();
      else focusSection();
    },
    [outbox, focusSection, liveNoteBoxRef],
  );
  const deleteNote = useCallback(
    async (note: EditableNote) => {
      const refusal = await removeNote(note);
      if (refusal === null) focusSection();
      return refusal;
    },
    [removeNote, focusSection],
  );
  // A failed transcription is reported, never shown as an empty transcript,
  // which would let a crashed ASR run pass for a silent meeting.
  // Only while the recording is in ERROR: a retry or speaker-inference run
  // moves it back to processing, and the old failure no longer applies then.
  const transcriptionError =
    transcriptStatus === "error" && recording.status === RecordingStatus.ERROR
      ? recording.transcript?.error_message || "Transcription failed."
      : null;

  return (
    <div
      ref={sectionRef}
      tabIndex={-1}
      role="tabpanel"
      id="panel-transcript"
      aria-labelledby="tab-transcript"
      className={`absolute inset-0 flex flex-col outline-none ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      {transcriptionError ? (
        <div
          role="alert"
          className="m-4 flex shrink-0 items-start gap-3 rounded-lg border border-status-danger-border bg-status-danger-bg p-4 text-sm text-status-danger-fg"
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div className="space-y-1">
            <p className="font-semibold">Transcription failed</p>
            <p>{transcriptionError}</p>
            <p>
              Use <strong>Retry Processing</strong> from this recording&apos;s
              menu in the recordings list to try again.
            </p>
          </div>
        </div>
      ) : null}
      {/* A note from the live note box that never saved, or text left in it,
          outlives processing: listed here until saved or discarded. */}
      {!liveTranscript ? <RecordingUnsavedNotes
        recordingId={recording.id}
        // With no lines to show a note on its line, every kind is listed here.
        kinds={transcriptSegments.length > 0 ? LISTED_NOTES : undefined}
        // Files show on their notes' lines, where there are lines.
        listUploads={transcriptSegments.length === 0}
        readEndMs={readEndMs}
        onEmptied={focusSection}
        className="mx-4 mt-4 max-h-[40%] shrink-0 overflow-y-auto border-b border-surface-divider pb-3"
      /> : null}
      {transcriptSegments.length > 0 || liveTranscript || hasShownLive ? (
        <div className="min-h-0 flex-1">
          <TranscriptView
            live={live}
            readOnly={readOnly}
            emptyStateTitle={transcriptionError ? "" : transcriptStatus === "completed"
              ? "No speech was detected in this recording." : "No transcript available yet."}
            recordingId={recording.id}
            segments={transcriptSegments}
            currentTime={currentTime}
            onPlaySegment={onPlaySegment}
            isPlaying={isPlaying}
            onPause={onPause}
            onResume={onResume}
            speakerMap={selectedSpeakerMap}
            speakers={liveTranscript?.speakers ?? recording.speakers ?? NO_SPEAKERS}
            globalSpeakers={globalSpeakers}
            onRenameSpeaker={(label, name) => canWrite() ? onRenameSpeaker(label, name) : false}
            onUpdateSegmentSpeaker={(segment, assignment) => canWrite() ? onUpdateSegmentSpeaker(segment, assignment) : false}
            onReviewSpeakerName={reviewSpeakerName}
            onUpdateSegmentText={(segment, text) => canWrite() ? onUpdateSegmentText(segment, text) : false}
            onSplitSegment={splitLine}
            onFindAndReplace={onFindAndReplace}
            speakerColors={speakerColors}
            onUndo={onUndo}
            onRedo={onRedo}
            canUndo={canUndo}
            canRedo={canRedo}
            onExport={onExport}
            disableSegmentPlayback={!canPlayback}
            disableExport={!canExport}
            onActiveEditUtteranceChange={onActiveEditUtteranceChange}
            pendingRemoteUtteranceIds={deferredTranscriptUtteranceIds}
            timelineNotes={liveTranscript && !isLiveCaptureInProgress(recording)
              ? notes.filter((note) => note.status === "saved") : notes}
            onAddNote={addNote}
            onRetryNote={outbox.retry}
            onDiscardNote={discardNote}
            onEditNote={liveTranscript ? undefined : editNote}
            onDeleteNote={liveTranscript ? undefined : deleteNote}
            onMoveNote={!liveTranscript || transcriptSegments.some((segment) => segment.settling_required) ? moveNote : undefined}
          />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center p-6 text-center space-y-4">
          {!transcriptIsAuthoritative && recording.transcript?.text ? (
            <>
              <div className="p-4 rounded-lg bg-surface-inset border border-surface-border max-w-md">
                <p className="text-lg font-medium text-contrast-muted">
                  {recording.transcript.text.replace(/[\[\]]/g, "")}
                </p>
              </div>
              {transcriptionError ? null : (
                <p className="text-sm text-contrast-helper">
                  The audio file was processed, but no speech segments were
                  generated.
                </p>
              )}
            </>
          ) : transcriptionError ? null : transcriptStatus === "completed" ? (
            // Transcription finished and heard nothing: a result, not a wait.
            <p className="text-contrast-helper italic">
              No speech was detected in this recording.
            </p>
          ) : (
            <p className="text-contrast-helper italic">
              No transcript available yet.
            </p>
          )}
        </div>
      )}
      {inFlightContent ? <div className="max-h-[50%] shrink-0 overflow-y-auto">{inFlightContent}</div> : null}
    </div>
  );
}
