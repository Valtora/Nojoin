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
import type { NoteSelection } from "@/lib/notes/noteOutbox";
import { ClientStatus } from "@/types";
import RecordingUnsavedNotes from "@/components/notes/RecordingUnsavedNotes";
import { inlineNotes, shownNotes, type OutboxDraft } from "@/lib/notes/noteOutbox";
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
const INLINE_NOTES = ["point", "selection"] as const;
const LISTED_NOTES = ["head", "tail"] as const;

interface TranscriptSectionProps {
  liveTranscript?: LiveTranscript;
  liveNoteBoxRef?: RefObject<HTMLTextAreaElement | null>;
  active: boolean;
  recording: Recording;
  inFlightContent?: ReactNode;
  canPlayback?: boolean;
  canExport?: boolean;
  transcriptIsAuthoritative?: boolean;
  /** A reprocess's publication is on screen, from its start until its result is read. */
  hasRetainedPublication?: boolean;
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
  hasRetainedPublication = false,
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
  const lineActions = useMemo(() => transcriptLineActions((message) =>
    addNotification({ type: "error", message })), [addNotification]);
  const applyLiveNames = liveTranscript?.applyLiveSpeakerNames ?? IGNORE_LIVE_NAMES;
  const liveNaming = useLiveSpeakerNaming(recording.id, applyLiveNames);
  const activeCapture = isLiveCaptureInProgress(recording);
  // Terminal metadata can precede the replacement read; an open editor can
  // defer its old row even after that read. Wait for the displayed replacement.
  const awaitingPublication = (!transcriptIsAuthoritative || deferredTranscriptUtteranceIds.length > 0) &&
    hasRetainedPublication;
  const readOnly = (isRecordingInFlight(recording) || awaitingPublication) && !activeCapture;
  // A finished recording keeps its finished presentation while a reprocess
  // reads the feed; capture keeps its live one through processing until handoff.
  const [presentFeed, setPresentFeed] = useState(usesLiveFeed);
  useOnDepsChange([usesLiveFeed, readOnly], () => {
    if (!usesLiveFeed) setPresentFeed(false);
    else if (!readOnly) setPresentFeed(true);
  });
  // The one handler guard: a control drawn before the view turned read-only
  // can still be used. Refresh follows the selected reader after a write.
  const refreshSelectedReader = useStableCallback(liveTranscript?.refresh ?? onRefresh);
  const nameReview = useSpeakerNameReview({ recordingId: recording.id, onRefresh });
  const reviewSpeakerName = async (label: string, name: string, decision: SpeakerNameDecision) => {
    if (!readOnly) await nameReview.review({ labels: [label], name }, decision);
  };
  const transcriptStatus = recording.transcript?.transcript_status;
  // The listed notes and those saved from this page since, less any it
  // discarded: the outbox outlives the live view, and its notes land here.
  // A note added here from a line's menu shows on its line while it saves;
  // the others still on their way are listed above the transcript.
  const { outbox, snapshot: outboxState } = useNoteOutbox(recording.id);
  // Edits, moves and deletes made here or in My notes show at once: a finished
  // recording's list is not fetched again.
  const { apply: applyEdits, edit: editNote, remove: removeNote, move: moveNote } = noteEdits;
  const notes = useMemo(
    () =>
      applyEdits([
        ...shownNotes(timelineNotes, outboxState).filter((note) => note.kind === "head" || note.kind === "tail"),
        ...inlineNotes(timelineNotes, outboxState, INLINE_NOTES),
      ]),
    [applyEdits, timelineNotes, outboxState],
  );
  const sectionRef = useRef<HTMLDivElement>(null);
  const durationSeconds = recording.duration_seconds;
  const readEndMs = useCallback(
    () => Math.round((durationSeconds ?? 0) * 1000),
    [durationSeconds],
  );
  const focusSection = useCallback(() => sectionRef.current?.focus(), []);
  const addNote = useCallback((note: OutboxDraft) => outbox.submit(note), [outbox]);
  const noteOnLine = useCallback((selection: NoteSelection) => {
    outbox.setDraft({ ...outbox.getSnapshot().draft, selection });
    liveNoteBoxRef?.current?.focus();
  }, [outbox, liveNoteBoxRef]);
  // On a line still settling: the box's next note is a typed note anchored at
  // the line's start, quoting nothing.
  const noteAtTime = useCallback((atMs: number) => {
    outbox.setDraft({
      ...outbox.getSnapshot().draft, selection: null, mode: "typing", typingStartMs: atMs, typingStartFromLine: true,
    });
    liveNoteBoxRef?.current?.focus();
  }, [outbox, liveNoteBoxRef]);
  const recordingId = recording.id;
  const liveLineHandlers = useMemo<LiveLineHandlers>(() => {
    const write = async (save: () => Promise<boolean>) => {
      if (readOnly) return false;
      const saved = await save();
      if (saved) refreshSelectedReader();
      return saved;
    };
    return {
      editText: (segment, text) => write(() => lineActions.editTranscriptLine(recordingId, segment, text)),
      changeSpeaker: (segment, assignment) =>
        write(() => lineActions.assignTranscriptLine(recordingId, segment, assignment)),
      split: (segment, wordId) => write(() => lineActions.splitTranscriptLine(recordingId, segment, wordId)),
    };
  }, [recordingId, refreshSelectedReader, readOnly, lineActions]);
  const paused = recording.status === RecordingStatus.PAUSED || recording.client_status === ClientStatus.PAUSED;
  const hasLoaded = liveTranscript?.hasLoaded ?? false;
  const liveBehind = activeCapture && (liveTranscript?.liveBehind ?? false);
  const live = useMemo<LiveTranscriptPresentation | undefined>(() => usesLiveFeed && presentFeed ? ({
    hasLoaded, isPaused: paused, liveBehind, lineHandlers: liveLineHandlers,
    onNoteOnLine: activeCapture ? noteOnLine : undefined,
    onNoteAtTime: activeCapture ? noteAtTime : undefined,
    onRenameLiveSpeaker: activeCapture ? liveNaming.renameLiveSpeaker : undefined,
    loadPeopleNames: liveNaming.loadPeopleNames,
  }) : undefined, [usesLiveFeed, presentFeed, hasLoaded, paused, liveBehind, liveLineHandlers, activeCapture,
    noteOnLine, noteAtTime, liveNaming.renameLiveSpeaker, liveNaming.loadPeopleNames]);
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
  // An in-flight recording's polls carry no transcript, so a retained one
  // first paints from its utterances read.
  const loadingRetained = hasRetainedPublication && !transcriptIsAuthoritative;
  const waitingText = loadingRetained ? "Loading the transcript." : "No transcript available yet.";

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
      <RecordingUnsavedNotes
        recordingId={recording.id}
        // With no lines to show a note on its line, every kind is listed here.
        kinds={transcriptSegments.length > 0 ? LISTED_NOTES : undefined}
        // Files show on their notes' lines, where there are lines.
        listUploads={transcriptSegments.length === 0}
        composerOwnsDraft={inFlightContent !== undefined}
        readEndMs={readEndMs}
        onEmptied={focusSection}
        className="mx-4 mt-4 max-h-[40%] shrink-0 overflow-y-auto border-b border-surface-divider pb-3"
      />
      {transcriptSegments.length > 0 || notes.length > 0 || liveTranscript ? (
        <div className="min-h-0 flex-1">
          <TranscriptView
            live={live}
            readOnly={readOnly}
            emptyStateTitle={transcriptionError ? "" : transcriptStatus === "completed"
              ? "No speech was detected in this recording." : waitingText}
            recordingId={recording.id}
            segments={transcriptSegments}
            currentTime={currentTime}
            onPlaySegment={onPlaySegment}
            isPlaying={isPlaying}
            onPause={onPause}
            onResume={onResume}
            speakerMap={speakerMap}
            speakers={liveTranscript?.speakers ?? recording.speakers ?? NO_SPEAKERS}
            globalSpeakers={globalSpeakers}
            onRenameSpeaker={(label, name) => !readOnly && onRenameSpeaker(label, name)}
            onUpdateSegmentSpeaker={(segment, assignment) => !readOnly && onUpdateSegmentSpeaker(segment, assignment)}
            onReviewSpeakerName={reviewSpeakerName}
            onUpdateSegmentText={(segment, text) => !readOnly && onUpdateSegmentText(segment, text)}
            onSplitSegment={liveLineHandlers.split}
            onFindAndReplace={onFindAndReplace}
            speakerColors={speakerColors}
            onUndo={onUndo}
            onRedo={onRedo}
            canUndo={!readOnly && canUndo}
            canRedo={!readOnly && canRedo}
            onExport={onExport}
            disableSegmentPlayback={!canPlayback}
            disableExport={!canExport}
            onActiveEditUtteranceChange={onActiveEditUtteranceChange}
            pendingRemoteUtteranceIds={deferredTranscriptUtteranceIds}
            timelineNotes={notes}
            onAddNote={addNote}
            onRetryNote={outbox.retry}
            onDiscardNote={discardNote}
            onEditNote={editNote}
            onDeleteNote={deleteNote}
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
              {waitingText}
            </p>
          )}
        </div>
      )}
      {/* Inset like the transcript above it, so the box's focus rings are not
          clipped, and clear of the phone's floating chat button. */}
      {inFlightContent ? <div data-testid="note-box-region"
        className="max-h-[50%] shrink-0 overflow-y-auto pb-2 pl-2 pr-clear-floating-2 md:pl-4 md:pr-clear-floating-4">
        {inFlightContent}
      </div> : null}
    </div>
  );
}
