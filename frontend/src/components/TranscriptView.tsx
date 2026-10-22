"use client";

import {
  TranscriptSegment,
  RecordingSpeaker,
  GlobalSpeaker,
  RecordingId,
  TimelineNote,
  TranscriptSpeakerAssignment,
} from "@/types";
import { Fragment, useMemo, useState } from "react";
import { Radio } from "lucide-react";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import { useTranscriptSearch } from "./transcript/_hooks/useTranscriptSearch";
import { useTranscriptScroll } from "./transcript/_hooks/useTranscriptScroll";
import { useTranscriptEditing } from "./transcript/_hooks/useTranscriptEditing";
import {
  buildTranscriptGroups,
  indexSegments,
  type IndexedTranscriptSegment,
} from "./transcript/transcriptGroups";
import { formatTime } from "./transcript/transcriptDisplay";
import TranscriptGroup from "./transcript/TranscriptGroup";
import TranscriptLine from "./transcript/TranscriptLine";
import TranscriptToolbar from "./transcript/TranscriptToolbar";
import { bodyNotesAmongLines, placeTimelineNotes } from "@/lib/notes/placement";
import TimelineNoteLine from "./notes/TimelineNoteLine";

interface TranscriptViewProps {
  recordingId: RecordingId;
  segments: TranscriptSegment[];
  currentTime: number;
  onPlaySegment: (start: number, end: number) => void;
  isPlaying: boolean;
  onPause: () => void;
  onResume: () => void;
  speakerMap: Record<string, string>;
  speakers: RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  onRenameSpeaker: (label: string, newName: string) => void | Promise<void>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | Promise<void>;
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
  pendingRemoteUtteranceIds?: string[];
  /** The recording's notes; point and selection notes show among the lines. */
  timelineNotes?: readonly TimelineNote[] | null;
}

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
  speakerMap,
  speakers,
  globalSpeakers,
  onRenameSpeaker,
  onUpdateSegmentSpeaker,
  onUpdateSegmentText,
  onFindAndReplace,
  speakerColors,
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
  pendingRemoteUtteranceIds = [],
  timelineNotes,
}: TranscriptViewProps) {
  // Find & Replace toolbar visibility (search match state lives in the hook)
  const [showSearch, setShowSearch] = useState(false);
  const [showReplace, setShowReplace] = useState(false);

  const pendingRemoteUtteranceIdSet = useMemo(
    () => new Set(pendingRemoteUtteranceIds),
    [pendingRemoteUtteranceIds],
  );

  const activeSegmentIndex = segments.findIndex(
    (s) => currentTime >= s.start && currentTime < s.end,
  );
  const activeSegmentKey =
    activeSegmentIndex >= 0
      ? getTranscriptSegmentKey(segments[activeSegmentIndex], activeSegmentIndex)
      : null;

  const {
    scrollContainerRef,
    activeSegmentRef,
    scrollSegmentIntoView,
    updateScrollAnchor,
  } = useTranscriptScroll({ segments, activeSegmentKey });

  const search = useTranscriptSearch({ segments, showSearch, scrollSegmentIntoView });

  const editing = useTranscriptEditing({
    segments,
    readOnly,
    allowProvisionalEdits,
    onRenameSpeaker,
    onUpdateSegmentSpeaker,
    onUpdateSegmentText,
    onFindAndReplace,
    onActiveEditUtteranceChange,
    search,
    onFindReplaceDone: () => {
      setShowReplace(false);
      setShowSearch(false);
    },
  });

  const hasKnownSpeakers = segments.some((s) => s.speaker !== "UNKNOWN");

  // Map to preserve original indices before filtering
  const indexedSegments = indexSegments(segments);

  const speakerFilteredSegments = hasKnownSpeakers
    ? indexedSegments.filter(
        ({ segment }) =>
          segment.speaker !== "UNKNOWN" || segment.provisional === true,
      )
    : indexedSegments;
  const displaySegments = speakerFilteredSegments;

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

  // Notes are placed on every line, hidden ones included, and each shows after
  // the visible line at or before the one it follows, so hiding a line never
  // hides a note. A note ends the group of the line it follows. Placed once
  // per change of lines or notes, not on every playback tick.
  const notePlacements = useMemo(
    () => placeTimelineNotes(segments, timelineNotes ?? []),
    [segments, timelineNotes],
  );
  const notesAmongLines = bodyNotesAmongLines(
    notePlacements,
    displaySegments.map((item) => item.index),
  );
  const renderNote = (note: TimelineNote) => (
    <div key={note.id} className="px-2">
      <TimelineNoteLine
        noteId={note.id}
        body={note.body}
        timeLabel={formatTime(
          (note.kind === "selection" ? (note.start_ms ?? 0) : (note.anchor_ms ?? 0)) / 1000,
        )}
        timeColumnClassName={NOTE_TIME_COLUMN}
      />
    </div>
  );

  // --- Grouping Logic ---
  const trackGroups = buildTranscriptGroups(
    displaySegments,
    new Set(notesAmongLines.afterLine.keys()),
  );

  const renderLine = (item: IndexedTranscriptSegment) => (
    <TranscriptLine
      key={item.segmentId}
      item={item}
      currentTime={currentTime}
      activeSegmentRef={activeSegmentRef}
      readOnly={readOnly}
      allowProvisionalEdits={allowProvisionalEdits}
      speakerMap={speakerMap}
      speakers={speakers}
      globalSpeakers={globalSpeakers}
      speakerColors={speakerColors}
      onUpdateSegmentSpeaker={onUpdateSegmentSpeaker}
      hasPendingRemoteUpdate={
        typeof item.segment.id === "string" &&
        pendingRemoteUtteranceIdSet.has(item.segment.id)
      }
      decorations={search.searchDecorations(item.segmentId)}
      editing={editing}
    />
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

            return (
              <Fragment key={groupKey || groupIndex}>
                <TranscriptGroup
                  group={group}
                  currentTime={currentTime}
                  isPlaying={isPlaying}
                  onPlaySegment={onPlaySegment}
                  onPause={onPause}
                  onResume={onResume}
                  disableSegmentPlayback={disableSegmentPlayback}
                  speakerDisplayOrder={speakerDisplayOrder}
                  speakerMap={speakerMap}
                  speakerColors={speakerColors}
                  renderLine={renderLine}
                />
                {notesAfterGroup.map(renderNote)}
              </Fragment>
            );
          }),
        ]}
      </div>
    </div>
  );
}
