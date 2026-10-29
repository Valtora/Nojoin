"use client";

import {
  TranscriptSegment,
  RecordingSpeaker,
  GlobalSpeaker,
  RecordingId,
  TimelineNote,
  TranscriptSpeakerAssignment,
} from "@/types";
import { Fragment, useCallback, useMemo, useState, type MouseEvent } from "react";
import { ArrowDown, Radio } from "lucide-react";
import { ButtonBase } from "./ui/nativeBases";
import { buildLiveTranscriptPresentation, liveRenderIdentity,
  type LiveTranscriptPresentation } from "./transcript/liveTranscriptPresentation";
import { useTranscriptSearch } from "./transcript/_hooks/useTranscriptSearch";
import { useTranscriptScroll } from "./transcript/_hooks/useTranscriptScroll";
import { useTranscriptEditing } from "./transcript/_hooks/useTranscriptEditing";
import { useStableCallback } from "./transcript/_hooks/useStableCallback";
import { useContentStable } from "./transcript/_hooks/useContentStable";
import { useRecentSegmentKeys } from "./transcript/_hooks/useRecentSegmentKeys";
import {
  buildTranscriptGroups,
  indexSegments,
  LINE_KEY_SEPARATOR,
  type IndexedTranscriptSegment,
} from "./transcript/transcriptGroups";
import { formatTime } from "./transcript/transcriptDisplay";
import TranscriptGroup, { type TranscriptLineShared } from "./transcript/TranscriptGroup";
import TranscriptSelectionNotes from "./transcript/TranscriptSelectionToolbar";
import TranscriptToolbar from "./transcript/TranscriptToolbar";
import TranscriptNoteEditor, {
  type EditingNote,
  type NoteEditHandlers,
} from "./transcript/TranscriptNoteEditor";
import { isSavedNote, useNoteHighlights } from "./transcript/_hooks/useNoteHighlights";
import { useNoteMoves, type MoveNoteHandler } from "./transcript/_hooks/useNoteMoves";
import { useLineNoteExpansion } from "./transcript/_hooks/useLineNoteExpansion";
import { bodyNotesAmongLines, placeTimelineNotes } from "@/lib/notes/placement";
import type { InlineNote, OutboxDraft } from "@/lib/notes/noteOutbox";
import type { SpeakerNameDecision } from "@/lib/speakerNameReview";
import CollapsedLineNotes from "./notes/CollapsedLineNotes";
import TimelineNoteLine from "./notes/TimelineNoteLine";
import { indexOverlaps } from "./transcript/OverlapIndicator";
import { appendPendingTranscriptGroups, isDiarisationPending } from "./transcript/pendingTranscriptGroups";
import { liveSpeakerDisplayName, segmentSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";

interface TranscriptViewProps {
  /** The selected in-flight reader, with its source-specific actions. */
  live?: LiveTranscriptPresentation;
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
  onRenameSpeaker: (label: string, newName: string) => void | boolean | Promise<void | boolean>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | boolean | Promise<void | boolean>;
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
  ) => void | boolean | Promise<void | boolean>;
  onSplitSegment?: (segment: TranscriptSegment, wordId: string) => Promise<boolean>;
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
  /** Playback and export have separate availability from transcript editing. */
  disableExport?: boolean;
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
  /** Moves a saved point note to a line's start; without it, notes stay put. */
  onMoveNote?: MoveNoteHandler;
}

// One empty list for every render, so the memoised lines it reaches hold.
const NO_PENDING_UPDATES: readonly string[] = [];

const NOTE_TIME_COLUMN =
  "min-w-16 md:min-w-[60px] shrink-0 pt-1 text-right font-mono text-sm text-contrast-icon-muted";

/**
 * The shared transcript: toolbar, then the lines in groups of overlapping
 * speech, with the recording's notes among them. The parts live in
 * `./transcript/`; this file filters the lines, places the notes, and wires
 * the state through.
 */
export default function TranscriptView({
  live,
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
  onSplitSegment,
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
  disableExport = false,
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
  onMoveNote,
}: TranscriptViewProps) {
  // Find & Replace toolbar visibility (search match state lives in the hook)
  const [showSearch, setShowSearch] = useState(false);
  const [showReplace, setShowReplace] = useState(false);
  const isLive = live !== undefined;
  const hasPendingDiarisation = segments.some(isDiarisationPending);
  const keepLineGroups = isLive || hasPendingDiarisation;
  const LineList = isLive ? "ol" : "div";
  const LineItem = isLive ? "li" : "div";
  const liveLines = useMemo(() => isLive ? buildLiveTranscriptPresentation(segments) : undefined, [segments, isLive]);

  // The recording page's poll hands these over anew every few seconds, most
  // often with the same contents; every line reads them.
  const speakerMap = useContentStable(speakerMapProp);
  const speakers = useContentStable(speakersProp);
  const liveSpeakerLabels = useContentStable(useMemo(() => Object.fromEntries(
    [...new Set(segments.map((segment) => segment.speaker))]
      .filter((label) => liveSpeakerDisplayName(label) !== null)
      .map((label) => [label, segmentSpeakerDisplayName(speakerMap, label)]),
  ), [segments, speakerMap]));
  const globalSpeakers = useContentStable(globalSpeakersProp);
  const speakerColors = useContentStable(speakerColorsProp);
  const pendingRemoteUtteranceIds = useContentStable(pendingRemoteUtteranceIdsProp);

  const pendingRemoteUtteranceIdSet = useMemo(
    () => new Set(pendingRemoteUtteranceIds),
    [pendingRemoteUtteranceIds],
  );
  const overlapBySegment = useMemo(() => indexOverlaps(segments), [segments]);

  // Memoised on the lines, so a playback tick hands every line the same item
  // and the same group, and an unchanged line does not redraw.
  const displaySegments = useMemo(() => {
    const hasKnownSpeakers = segments.some((s) => s.speaker !== "UNKNOWN");

    // Map to preserve original indices before filtering
    const indexedSegments = indexSegments(segments);

    return hasKnownSpeakers && !isLive
      ? indexedSegments.filter(
          ({ segment }) =>
            segment.speaker !== "UNKNOWN" || segment.provisional === true || isDiarisationPending(segment),
        )
      : indexedSegments;
  }, [segments, isLive]);

  const recentSegmentKeys = new Set(useRecentSegmentKeys(displaySegments).split(LINE_KEY_SEPARATOR));

  // Playback-follow keeps the first shown line playback is in in view.
  const activeSegmentKey =
    displaySegments.find(
      ({ segment }) => currentTime >= segment.start && currentTime < segment.end,
    )?.segmentId ?? null;

  const {
    scrollContainerRef,
    scrollSegmentIntoView,
    updateScrollAnchor,
    isFollowingLatest,
    jumpToLatest,
  } = useTranscriptScroll({ segments, activeSegmentKey, liveFollow: !!live, growth: timelineNotes });

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
  const lineNotes = useLineNoteExpansion();
  const visibleIndexes = useMemo(() => displaySegments.map((item) => item.index), [displaySegments]);
  const notesAmongLines = useMemo(
    () => bodyNotesAmongLines(notePlacements, visibleIndexes),
    [notePlacements, visibleIndexes],
  );
  const headNotes = notePlacements.filter((placement) => placement.slot === "head").map(({ note }) => note);
  const tailNotes = notePlacements.filter((placement) => placement.slot === "tail").map(({ note }) => note);
  const moves = useNoteMoves({
    segments,
    visibleIndexes,
    placements: notePlacements,
    regionRef: scrollContainerRef,
    onMoveNote,
  });
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
  const openNoteEditor = useCallback((next: EditingNote) =>
    setEditingNote((open) => (open !== null && open === shownEditor ? open : next)), [shownEditor, setEditingNote]);
  const hasNoteEditor = onEditNote !== undefined;
  const canRetryNote = onRetryNote !== undefined;
  const canDiscardNote = onDiscardNote !== undefined;
  const retryNote = useStableCallback((key: string) => onRetryNote?.(key));
  const discardNote = useStableCallback((key: string) => onDiscardNote?.(key));
  const canEditNote = useCallback((note: TimelineNote | InlineNote) =>
    hasNoteEditor && isSavedNote(note) && note.revision !== undefined, [hasNoteEditor]);
  const { controlsFor, gapOf, focusAfterRetry, hasRefusal } = moves;
  const isFoldable = useCallback((note: TimelineNote | InlineNote) =>
    (!("status" in note) || note.status !== "failed") && !hasRefusal(note.id) && shownEditor?.noteId !== note.id,
  [hasRefusal, shownEditor]);
  const toggleLineNotes = useStableCallback(lineNotes.toggle);
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
  const renderNote = useCallback((note: TimelineNote | InlineNote) => {
    const key = "key" in note ? note.key : note.id;
    return (
      <LineItem key={key} className="px-2" data-testid={live ? "live-transcript-note" : undefined} data-note-gap={gapOf(note.id)}>
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
          placementLabel={note.kind === "head" ? "Before the transcript"
            : note.kind === "tail" ? "After the transcript" : undefined}
          timeColumnClassName={NOTE_TIME_COLUMN}
          status={"status" in note ? note.status : "saved"}
          error={"error" in note ? note.error : null}
          onRetry={canRetryNote ? () => { focusAfterRetry(note.id); retryNote(key); } : undefined}
          onDiscard={canDiscardNote ? () => discardNote(key) : undefined}
          // Not while its editor is open: a move would take the pencil it sits against.
          move={controlsFor(note, (live ? isSavedNote(note) && note.revision !== undefined : canEditNote(note)) && shownEditor?.noteId !== note.id)}
          attachment={note.attachment}
        />
      </LineItem>
    );
  }, [LineItem, live, gapOf, wordsChanged, canEditNote, openNoteEditor, canRetryNote, canDiscardNote,
    retryNote, discardNote, focusAfterRetry, controlsFor, shownEditor]);

  // --- Grouping Logic ---
  const trackGroups = useMemo(
    () => {
      const groups = buildTranscriptGroups(
        displaySegments,
        new Set(keepLineGroups ? displaySegments.map((item) => item.index) : notesAmongLines.afterLine.keys()),
      );
      const blockedIds = new Set<string>();
      if (editing.editingTextId) blockedIds.add(editing.editingTextId);
      if (editing.editingSpeakerLineId) blockedIds.add(editing.editingSpeakerLineId);
      if (editing.activePopover) blockedIds.add(editing.activePopover.segmentId);
      return appendPendingTranscriptGroups(groups, new Set(notesAmongLines.afterLine.keys()), blockedIds);
    },
    [displaySegments, notesAmongLines, keepLineGroups,
      editing.editingTextId, editing.editingSpeakerLineId, editing.activePopover],
  );

  // Groups and lines are memoised: everything handed to them is a value, or
  // keeps its identity until it means something new, so a playback tick
  // redraws only the groups and lines it enters or leaves. The recording page
  // builds these four handlers anew on every tick; the lines get stable ones.
  const playSegment = useStableCallback((start: number, end?: number) => {
    if (!disableSegmentPlayback) onPlaySegment(start, end);
  });
  const pause = useStableCallback(onPause);
  const resume = useStableCallback(onResume);
  const updateSegmentSpeaker = useStableCallback(onUpdateSegmentSpeaker);
  const canReviewSpeakerName = onReviewSpeakerName !== undefined;
  const reviewSpeakerName = useStableCallback(
    async (label: string, name: string, decision: SpeakerNameDecision) =>
      onReviewSpeakerName?.(label, name, decision),
  );
  // "Play from here" on a line plays on, rather than to the group's end.
  const playFrom = useStableCallback((startSeconds: number) => {
    if (!disableSegmentPlayback) onPlaySegment(startSeconds);
  });
  const canAddNote = onAddNote !== undefined;
  const addNote = useStableCallback((note: OutboxDraft) => onAddNote?.(note) ?? null);
  const canSplit = onSplitSegment !== undefined;
  const splitSegment = useStableCallback(async (segment: TranscriptSegment, wordId: string) =>
    await onSplitSegment?.(segment, wordId) ?? false);
  const lineShared = useMemo<TranscriptLineShared>(
    () => ({
      readOnly,
      allowProvisionalEdits,
      live,
      liveLines,
      preserveLiveIdentity: keepLineGroups,
      speakerMap,
      speakers,
      liveSpeakerLabels,
      globalSpeakers,
      speakerColors,
      onUpdateSegmentSpeaker: updateSegmentSpeaker,
      onReviewSpeakerName: canReviewSpeakerName ? reviewSpeakerName : null,
      pendingRemoteUtteranceIds: pendingRemoteUtteranceIdSet,
      actions: editing.lineActions,
      onAddNote: canAddNote ? addNote : null,
      onPlayFrom: disableSegmentPlayback ? null : playFrom,
      onSplitSegment: canSplit ? splitSegment : null,
    }),
    [
      readOnly,
      allowProvisionalEdits,
      live,
      liveLines,
      keepLineGroups,
      speakerMap,
      speakers,
      liveSpeakerLabels,
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
      canSplit,
      splitSegment,
    ],
  );
  const lineKeys = (
    items: readonly IndexedTranscriptSegment[],
    keep: (segment: TranscriptSegment, segmentId: string) => boolean,
  ) =>
    items
      .filter(({ segment, segmentId }) => keep(segment, segmentId))
      .map(({ segmentId }) => segmentId)
      .join(LINE_KEY_SEPARATOR);
  const holdsTheEditor = (items: readonly IndexedTranscriptSegment[]) =>
    items.some(
      ({ segmentId }) =>
        segmentId === editing.editingSpeakerLineId || segmentId === editing.editingTextId,
    );

  return (
    <div id="transcript-view" data-testid={live ? "live-transcript-panel" : undefined}
      data-paused={live ? String(!!live.isPaused) : undefined} className="flex flex-col h-full relative min-h-0">
      <TranscriptToolbar
        onUndo={onUndo}
        onRedo={onRedo}
        canUndo={!live && canUndo}
        canRedo={!live && canRedo}
        mutationsDisabled={!!live || readOnly}
        onExport={onExport}
        exportDisabled={disableExport || editing.editInProgress}
        exportUnavailable={disableExport}
        showSearch={showSearch}
        setShowSearch={setShowSearch}
        showReplace={showReplace}
        setShowReplace={setShowReplace}
        search={search}
        isSubmitting={editing.isSubmitting}
        onReplaceCurrent={editing.handleReplaceCurrent}
        onReplaceAll={editing.handleFindReplaceSubmit}
      />

      {live && segments.length > 0 ? <p className="px-4 py-2 text-xs text-contrast-helper">
        {segments.some((segment) => segment.settling_required)
          ? "Lines settle as the conversation continues. Your corrections are kept."
          : "Provisional. Corrected when the recording is processed."}
      </p> : null}
      {live?.liveBehind && !live.isPaused ? <p role="status" className="px-4 py-2 text-sm text-contrast-helper">
        Live transcript is behind the recording. Nothing is lost: the full transcript is made after the meeting ends.
      </p> : null}

      <div
        ref={scrollContainerRef}
        data-testid={live ? "live-transcript-scroll" : "transcript-scroll-region"}
        tabIndex={-1}
        className="relative px-2 md:px-4 pt-3 pb-clear-floating-3 overflow-y-auto flex-1 min-h-0"
        onScroll={updateScrollAnchor}
        onClick={editNoteFromText}
      >
        {/* First, so the region's spacing between the lines stays as it is. */}
        {moves.drag?.bar ? (
          <div
            aria-hidden="true"
            data-testid="note-drop-bar"
            className="pointer-events-none absolute h-0.5 rounded-pill bg-action"
            style={{ top: moves.drag.bar.top, left: moves.drag.bar.left, width: moves.drag.bar.width }}
          />
        ) : null}
        {trackGroups.length === 0 && notesAmongLines.opening.length === 0 && headNotes.length === 0 && tailNotes.length === 0 ? (
          <div className="flex h-full min-h-[220px] items-center justify-center px-4">
            <div className="flex max-w-sm flex-col items-center text-center">
              <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-action-border bg-action-tint text-action-text">
                <Radio className="h-4 w-4" />
              </div>
              <div className="text-sm font-semibold text-foreground">
                {live?.capturing ? live.isPaused ? "Recording is paused." : "Listening." : emptyStateTitle}
              </div>
              {(live?.capturing || emptyStateDescription) && (
                <div className="mt-1 text-sm leading-5 text-contrast-helper">
                  {live?.capturing ? live.hasLoaded
                    ? "Text appears a few seconds behind the conversation, as each sentence completes."
                    : "Loading the transcript so far." : emptyStateDescription}
                </div>
              )}
            </div>
          </div>
        ) : <LineList className="space-y-4">{[
          ...headNotes.map(renderNote),
          ...notesAmongLines.opening.map(renderNote),
          ...trackGroups.map((group, groupIndex) => {
            const first = group.items[0];
            const last = group.items[group.items.length - 1];
            const noteLineId = liveRenderIdentity(last.segment, last.index);
            const groupKey = keepLineGroups ? liveRenderIdentity(first.segment, first.index)
              : group.items.map((item) => item.segmentId).join("|");
            const notesAfterGroup = notesAmongLines.afterLine.get(last.index) ?? [];
            const isGroupActive = currentTime >= group.start && currentTime < group.end;

            return (
              <Fragment key={groupKey || groupIndex}>
                <LineItem>
                <TranscriptGroup
                  group={group}
                  overlaps={overlapBySegment}
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
                  recentLineKeys={lineKeys(group.items, (_segment, segmentId) => recentSegmentKeys.has(segmentId))}
                  searchDecorations={search.searchDecorations}
                  noteDecorations={noteDecorations}
                  editingSpeakerLineId={editing.editingSpeakerLineId}
                  editingTextId={editing.editingTextId}
                  activePopover={editing.activePopover}
                  editor={holdsTheEditor(group.items) ? editing.editor : null}
                />
                </LineItem>
                {notesAfterGroup.length > 0 ? <CollapsedLineNotes
                  as={LineItem}
                  notes={notesAfterGroup}
                  isFoldable={isFoldable}
                  open={lineNotes.isExpanded(noteLineId)}
                  lineId={noteLineId}
                  onToggle={toggleLineNotes}
                  gapIndex={last.index}
                  saving={notesAfterGroup.some((note) => "status" in note && note.status === "saving")}
                  renderNote={renderNote}
                  lineTime={formatTime(last.segment.start)}
                  timeColumnClassName={NOTE_TIME_COLUMN}
                  className="px-2"
                /> : null}
              </Fragment>
            );
          }),
          ...tailNotes.map(renderNote),
        ]}</LineList>}
      </div>
      {live && !isFollowingLatest ? <div className="pointer-events-none absolute bottom-4 left-0 right-0 flex justify-center">
        <ButtonBase
          data-ui-fill="card"
          data-ui-hover="action-tint"
          data-ui-border="full"
          chrome="action"
          type="button"
          onClick={jumpToLatest}
          className="pointer-events-auto inline-flex items-center gap-1.5 rounded-pill border border-action-border bg-surface-card px-3 py-1.5 text-xs font-semibold text-action-text shadow-float transition-colors hover:bg-action-tint"
        >
          <ArrowDown aria-hidden className="h-3.5 w-3.5" /> Jump to latest
        </ButtonBase>
      </div> : null}
      <p role="status" className="sr-only">
        {moves.announcement}
      </p>
      <TranscriptSelectionNotes root={scrollContainerRef} onNoteOnText={live?.onNoteOnLine}
        onAddNote={!live && onAddNote ? addNote : undefined} segments={segments} />
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
