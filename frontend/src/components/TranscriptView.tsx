"use client";

import {
  TranscriptSegment,
  RecordingSpeaker,
  GlobalSpeaker,
  RecordingId,
  TimelineNote,
  TranscriptSpeakerAssignment,
} from "@/types";
import { Fragment, useMemo, useState, type MouseEvent } from "react";
import { Radio } from "lucide-react";
import { useTranscriptSearch } from "./transcript/_hooks/useTranscriptSearch";
import { useTranscriptScroll } from "./transcript/_hooks/useTranscriptScroll";
import { useTranscriptEditing } from "./transcript/_hooks/useTranscriptEditing";
import { useStableCallback } from "./transcript/_hooks/useStableCallback";
import { useContentStable } from "./transcript/_hooks/useContentStable";
import {
  buildTranscriptGroups,
  indexSegments,
  LINE_KEY_SEPARATOR,
  type IndexedTranscriptSegment,
} from "./transcript/transcriptGroups";
import { formatTime, isRecentlyUpdatedSegment } from "./transcript/transcriptDisplay";
import TranscriptGroup, { type TranscriptLineShared } from "./transcript/TranscriptGroup";
import TranscriptSelectionNotes from "./transcript/TranscriptSelectionToolbar";
import TranscriptToolbar from "./transcript/TranscriptToolbar";
import TranscriptNoteEditor, {
  type EditingNote,
  type NoteEditHandlers,
} from "./transcript/TranscriptNoteEditor";
import { isSavedNote, useNoteHighlights } from "./transcript/_hooks/useNoteHighlights";
import { bodyNotesAmongLines, placeTimelineNotes } from "@/lib/notes/placement";
import type { InlineNote, OutboxDraft } from "@/lib/notes/noteOutbox";
import type { SpeakerNameDecision } from "@/lib/speakerNameReview";
import CollapsedLineNotes from "./notes/CollapsedLineNotes";
import TimelineNoteLine from "./notes/TimelineNoteLine";

interface TranscriptViewProps {
  recordingId: RecordingId;
  segments: TranscriptSegment[];
  currentTime: number;
  /** Plays from `start` to `end` in seconds, or on from `start` without one. */
  onPlaySegment: (start: number, end?: number) => void;
  isPlaying: boolean;
  onPause: () => void;
  onResume: () => void;
  speakerMap: Record<string, string>;
  speakers: readonly RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | Promise<void>;
  /**
   * Confirms or dismisses a speaker's suggested name; without it, lines show
   * no "?" for one.
   */
  onReviewSpeakerName?: (
    label: string,
    name: string,
    decision: SpeakerNameDecision,
  ) => Promise<void>;
  onUpdateSegmentText: (
    segment: TranscriptSegment,
    text: string,
  ) => void | Promise<void>;
  onFindAndReplace: (
    find: string,
    replace: string,
    options?: { caseSensitive?: boolean; useRegex?: boolean },
  ) => void | Promise<void>;
  speakerColors: Record<string, string>;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onExport: () => void;
  readOnly?: boolean;
  allowProvisionalEdits?: boolean;
  disableSegmentPlayback?: boolean;
  emptyStateTitle?: string;
  emptyStateDescription?: string;
  onActiveEditUtteranceChange?: (utteranceId: string | null) => void;
  pendingRemoteUtteranceIds?: readonly string[];
  /**
   * The recording's notes; point and selection notes show among the lines,
   * with their state while one is still on its way.
   */
  timelineNotes?: readonly (TimelineNote | InlineNote)[] | null;
  /** Queues a note from a line's menu; without it the menu offers none. */
  onAddNote?: (note: OutboxDraft) => string | null;
  onRetryNote?: (key: string) => void;
  /** Removes a failed note's line: the caller moves focus somewhere that stays. */
  onDiscardNote?: (key: string) => void;
  /** Saves a saved note's new text; without it, notes can't be edited here. */
  onEditNote?: NoteEditHandlers["onEditNote"];
  /** Deletes a saved note: the caller moves focus somewhere that stays. */
  onDeleteNote?: NoteEditHandlers["onDeleteNote"];
}

// One empty list for every render, so the memoised lines it reaches hold.
const NO_PENDING_UPDATES: readonly string[] = [];

const NOTE_TIME_COLUMN =
  "min-w-16 md:min-w-[60px] shrink-0 pt-1 text-right font-mono text-sm text-contrast-icon-muted";

/**
 * The finished transcript: toolbar, then the lines in groups of overlapping
 * speech, with the recording's notes among them. The parts live in
 * `./transcript/`; this file filters the lines, places the notes, and wires
 * the state through.
 */
export default function TranscriptView({
  segments,
  currentTime,
  onPlaySegment,
  isPlaying,
  onPause,
  onResume,
  speakerMap: speakerMapProp,
  speakers: speakersProp,
  globalSpeakers: globalSpeakersProp,
  onRenameSpeaker,
  onUpdateSegmentSpeaker,
  onReviewSpeakerName,
  onUpdateSegmentText,
  onFindAndReplace,
  speakerColors: speakerColorsProp,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  onExport,
  readOnly = false,
  allowProvisionalEdits = false,
  disableSegmentPlayback = false,
  emptyStateTitle = "No transcript segments",
  emptyStateDescription,
  onActiveEditUtteranceChange,
  pendingRemoteUtteranceIds: pendingRemoteUtteranceIdsProp = NO_PENDING_UPDATES,
  timelineNotes,
  onAddNote,
  onRetryNote,
  onDiscardNote,
  onEditNote,
  onDeleteNote,
}: TranscriptViewProps) {
  // Find & Replace toolbar visibility (search match state lives in the hook)
  const [showSearch, setShowSearch] = useState(false);
  const [showReplace, setShowReplace] = useState(false);

  // The recording page's poll hands these over anew every few seconds, most
  // often with the same contents; every line reads them.
  const speakerMap = useContentStable(speakerMapProp);
  const speakers = useContentStable(speakersProp);
  const globalSpeakers = useContentStable(globalSpeakersProp);
  const speakerColors = useContentStable(speakerColorsProp);
  const pendingRemoteUtteranceIds = useContentStable(pendingRemoteUtteranceIdsProp);

  const pendingRemoteUtteranceIdSet = useMemo(
    () => new Set(pendingRemoteUtteranceIds),
    [pendingRemoteUtteranceIds],
  );

  // Memoised on the lines, so a playback tick hands every line the same item
  // and the same group, and an unchanged line does not redraw.
  const displaySegments = useMemo(() => {
    const hasKnownSpeakers = segments.some((s) => s.speaker !== "UNKNOWN");

    // Map to preserve original indices before filtering
    const indexedSegments = indexSegments(segments);

    return hasKnownSpeakers
      ? indexedSegments.filter(
          ({ segment }) =>
            segment.speaker !== "UNKNOWN" || segment.provisional === true,
        )
      : indexedSegments;
  }, [segments]);

  // Playback-follow keeps the first shown line playback is in in view.
  const activeSegmentKey =
    displaySegments.find(
      ({ segment }) => currentTime >= segment.start && currentTime < segment.end,
    )?.segmentId ?? null;

  const {
    scrollContainerRef,
    scrollSegmentIntoView,
    updateScrollAnchor,
  } = useTranscriptScroll({ segments, activeSegmentKey });

  const search = useTranscriptSearch({ segments, showSearch, scrollSegmentIntoView });

  const editing = useTranscriptEditing({
    segments,
    readOnly,
    allowProvisionalEdits,
    onRenameSpeaker,
    onUpdateSegmentText,
    onFindAndReplace,
    onActiveEditUtteranceChange,
    search,
    onFindReplaceDone: () => {
      setShowReplace(false);
      setShowSearch(false);
    },
  });

  const speakerDisplayOrder = useMemo(() => {
    const order = new Map<string, number>();
    let nextIndex = 0;

    displaySegments.forEach(({ segment }) => {
      [segment.speaker, ...(segment.overlapping_speakers || [])].forEach(
        (speakerLabel) => {
          if (!order.has(speakerLabel)) {
            order.set(speakerLabel, nextIndex);
            nextIndex += 1;
          }
        },
      );
    });

    return order;
  }, [displaySegments]);

  // Each speaker's first final line, the one that offers a suggested name's
  // "?" (a provisional line offers none).
  const speakerFirstLines = useMemo(() => {
    const firstLines = new Map<string, string>();
    for (const { segment, segmentId } of displaySegments) {
      if (segment.provisional === true || firstLines.has(segment.speaker)) continue;
      firstLines.set(segment.speaker, segmentId);
    }
    return firstLines;
  }, [displaySegments]);

  // Notes are placed on every line, hidden ones included, and each shows after
  // the visible line at or before the one it follows, so hiding a line never
  // hides a note. A note ends the group of the line it follows. Placed once
  // per change of lines or notes, not on every playback tick.
  const notePlacements = useMemo(
    () => placeTimelineNotes(segments, timelineNotes ?? []),
    [segments, timelineNotes],
  );
  const notesAmongLines = useMemo(
    () =>
      bodyNotesAmongLines(
        notePlacements,
        displaySegments.map((item) => item.index),
      ),
    [notePlacements, displaySegments],
  );
  // Each saved selection note's words, found in the lines as they read now.
  const { noteDecorations, wordsChanged } = useNoteHighlights(segments, timelineNotes);
  const [editingNote, setEditingNote] = useState<EditingNote | null>(null);
  // The editor shows only while its note is a saved one in the list.
  const shownEditor =
    editingNote !== null &&
    (timelineNotes ?? []).some((note) => note.id === editingNote.noteId && isSavedNote(note))
      ? editingNote
      : null;
  // An editor still open holds typed text or a write, so another note's marks
  // or pencil never replace it; one left as it opened has already closed on
  // the press (`NoteOnSelectionPopover`).
  const openNoteEditor = (next: EditingNote) =>
    setEditingNote((open) => (open !== null && open === shownEditor ? open : next));
  const canEditNote = (note: TimelineNote | InlineNote) =>
    onEditNote !== undefined && isSavedNote(note) && note.revision !== undefined;
  // A click on a note's marked words edits the note, unless it ended a
  // drag that selected text, which is for a new note or a copy.
  const editNoteFromText = (event: MouseEvent<HTMLDivElement>) => {
    const mark =
      event.target instanceof Element ? event.target.closest<HTMLElement>("mark[data-note-id]") : null;
    const noteId = mark?.dataset.noteId;
    if (!mark || !noteId || !onEditNote) return;
    if (window.getSelection()?.isCollapsed === false) return;
    openNoteEditor({ noteId, anchor: mark });
  };
  const renderNote = (note: TimelineNote | InlineNote) => {
    const key = "key" in note ? note.key : note.id;
    return (
      <div key={key} className="px-2">
        <TimelineNoteLine
          noteId={note.id}
          body={note.body}
          quote={note.kind === "selection" ? note.quoted_text : null}
          wordsChanged={note.kind === "selection" ? wordsChanged(note.id) : null}
          onEdit={
            canEditNote(note)
              ? (trigger) => openNoteEditor({ noteId: note.id, anchor: trigger })
              : undefined
          }
          timeLabel={formatTime(
            (note.kind === "selection" ? (note.start_ms ?? 0) : (note.anchor_ms ?? 0)) / 1000,
          )}
          timeColumnClassName={NOTE_TIME_COLUMN}
          status={"status" in note ? note.status : "saved"}
          error={"error" in note ? note.error : null}
          onRetry={onRetryNote ? () => onRetryNote(key) : undefined}
          onDiscard={onDiscardNote ? () => onDiscardNote(key) : undefined}
        />
      </div>
    );
  };

  // --- Grouping Logic ---
  const trackGroups = useMemo(
    () =>
      buildTranscriptGroups(
        displaySegments,
        new Set(notesAmongLines.afterLine.keys()),
      ),
    [displaySegments, notesAmongLines],
  );

  // Groups and lines are memoised: everything handed to them is a value, or
  // keeps its identity until it means something new, so a playback tick
  // redraws only the groups and lines it enters or leaves. The recording page
  // builds these four handlers anew on every tick; the lines get stable ones.
  const playSegment = useStableCallback(onPlaySegment);
  const pause = useStableCallback(onPause);
  const resume = useStableCallback(onResume);
  const updateSegmentSpeaker = useStableCallback(onUpdateSegmentSpeaker);
  const canReviewSpeakerName = onReviewSpeakerName !== undefined;
  const reviewSpeakerName = useStableCallback(
    async (label: string, name: string, decision: SpeakerNameDecision) =>
      onReviewSpeakerName?.(label, name, decision),
  );
  // "Play from here" on a line plays on, rather than to the group's end.
  const playFrom = useStableCallback((startSeconds: number) => onPlaySegment(startSeconds));
  const canAddNote = onAddNote !== undefined;
  const addNote = useStableCallback((note: OutboxDraft) => onAddNote?.(note) ?? null);
  const lineShared = useMemo<TranscriptLineShared>(
    () => ({
      readOnly,
      allowProvisionalEdits,
      speakerMap,
      speakers,
      globalSpeakers,
      speakerColors,
      onUpdateSegmentSpeaker: updateSegmentSpeaker,
      onReviewSpeakerName: canReviewSpeakerName ? reviewSpeakerName : null,
      pendingRemoteUtteranceIds: pendingRemoteUtteranceIdSet,
      actions: editing.lineActions,
      onAddNote: canAddNote ? addNote : null,
      onPlayFrom: disableSegmentPlayback ? null : playFrom,
    }),
    [
      readOnly,
      allowProvisionalEdits,
      speakerMap,
      speakers,
      globalSpeakers,
      speakerColors,
      updateSegmentSpeaker,
      canReviewSpeakerName,
      reviewSpeakerName,
      pendingRemoteUtteranceIdSet,
      editing.lineActions,
      canAddNote,
      addNote,
      disableSegmentPlayback,
      playFrom,
    ],
  );
  const lineKeys = (
    items: readonly IndexedTranscriptSegment[],
    keep: (segment: TranscriptSegment) => boolean,
  ) =>
    items
      .filter(({ segment }) => keep(segment))
      .map(({ segmentId }) => segmentId)
      .join(LINE_KEY_SEPARATOR);
  const holdsTheEditor = (items: readonly IndexedTranscriptSegment[]) =>
    items.some(
      ({ segmentId }) =>
        segmentId === editing.editingSpeakerLineId || segmentId === editing.editingTextId,
    );

  return (
    <div id="transcript-view" className="flex flex-col h-full relative min-h-0">
      <TranscriptToolbar
        onUndo={onUndo}
        onRedo={onRedo}
        canUndo={canUndo}
        canRedo={canRedo}
        onExport={onExport}
        exportDisabled={editing.editInProgress}
        showSearch={showSearch}
        setShowSearch={setShowSearch}
        showReplace={showReplace}
        setShowReplace={setShowReplace}
        search={search}
        isSubmitting={editing.isSubmitting}
        onReplaceCurrent={editing.handleReplaceCurrent}
        onReplaceAll={editing.handleFindReplaceSubmit}
      />

      <div
        ref={scrollContainerRef}
        data-testid="transcript-scroll-region"
        className="space-y-4 px-2 md:px-4 pt-3 pb-clear-floating-3 overflow-y-auto flex-1 min-h-0"
        onScroll={updateScrollAnchor}
        onClick={editNoteFromText}
      >
        {trackGroups.length === 0 ? (
          <div className="flex h-full min-h-[220px] items-center justify-center px-4">
            <div className="flex max-w-sm flex-col items-center text-center">
              <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-action-border bg-action-tint text-action-text">
                <Radio className="h-4 w-4" />
              </div>
              <div className="text-sm font-semibold text-foreground">
                {emptyStateTitle}
              </div>
              {emptyStateDescription && (
                <div className="mt-1 text-sm leading-5 text-contrast-helper">
                  {emptyStateDescription}
                </div>
              )}
            </div>
          </div>
        ) : [
          ...notesAmongLines.opening.map(renderNote),
          ...trackGroups.map((group, groupIndex) => {
            const groupKey = group.items.map((item) => item.segmentId).join("|");
            const notesAfterGroup =
              notesAmongLines.afterLine.get(group.items[group.items.length - 1].index) ?? [];
            const isGroupActive = currentTime >= group.start && currentTime < group.end;

            return (
              <Fragment key={groupKey || groupIndex}>
                <TranscriptGroup
                  group={group}
                  isGroupActive={isGroupActive}
                  // Only the group playback is in shows play or pause, so
                  // only it redraws when playback starts or stops.
                  isPlaying={isPlaying && isGroupActive}
                  onPlaySegment={playSegment}
                  onPause={pause}
                  onResume={resume}
                  disableSegmentPlayback={disableSegmentPlayback}
                  speakerDisplayOrder={speakerDisplayOrder}
                  speakerFirstLines={speakerFirstLines}
                  lineShared={lineShared}
                  activeLineKeys={lineKeys(
                    group.items,
                    (segment) => currentTime >= segment.start && currentTime < segment.end,
                  )}
                  recentLineKeys={lineKeys(group.items, isRecentlyUpdatedSegment)}
                  searchDecorations={search.searchDecorations}
                  noteDecorations={noteDecorations}
                  editingSpeakerLineId={editing.editingSpeakerLineId}
                  editingTextId={editing.editingTextId}
                  activePopover={editing.activePopover}
                  editor={holdsTheEditor(group.items) ? editing.editor : null}
                />
                <CollapsedLineNotes
                  notes={notesAfterGroup}
                  isSaved={isSavedNote}
                  renderNote={renderNote}
                  lineTime={() => formatTime(group.items[group.items.length - 1].segment.start)}
                  timeColumnClassName={NOTE_TIME_COLUMN}
                  className="px-2"
                />
              </Fragment>
            );
          }),
        ]}
      </div>
      <TranscriptSelectionNotes root={scrollContainerRef} onAddNote={onAddNote ? addNote : undefined} />
      {onEditNote ? (
        <TranscriptNoteEditor
          editing={shownEditor}
          notes={timelineNotes}
          onEditNote={onEditNote}
          onDeleteNote={onDeleteNote}
          onClose={() => setEditingNote(null)}
        />
      ) : null}
    </div>
  );
}
