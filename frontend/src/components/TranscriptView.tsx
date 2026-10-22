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
  speakers: readonly RecordingSpeaker[];
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
  pendingRemoteUtteranceIds?: readonly string[];
  /** The recording's notes; point and selection notes show among the lines. */
  timelineNotes?: readonly TimelineNote[] | null;
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
  const lineShared = useMemo<TranscriptLineShared>(
    () => ({
      readOnly,
      allowProvisionalEdits,
      speakerMap,
      speakers,
      globalSpeakers,
      speakerColors,
      onUpdateSegmentSpeaker: updateSegmentSpeaker,
      pendingRemoteUtteranceIds: pendingRemoteUtteranceIdSet,
      actions: editing.lineActions,
    }),
    [
      readOnly,
      allowProvisionalEdits,
      speakerMap,
      speakers,
      globalSpeakers,
      speakerColors,
      updateSegmentSpeaker,
      pendingRemoteUtteranceIdSet,
      editing.lineActions,
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
      ({ segment, segmentId }) =>
        segment.speaker === editing.editingSpeaker || segmentId === editing.editingTextId,
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
                  lineShared={lineShared}
                  activeLineKeys={lineKeys(
                    group.items,
                    (segment) => currentTime >= segment.start && currentTime < segment.end,
                  )}
                  recentLineKeys={lineKeys(group.items, isRecentlyUpdatedSegment)}
                  searchDecorations={search.searchDecorations}
                  editingSpeaker={editing.editingSpeaker}
                  editingTextId={editing.editingTextId}
                  activePopover={editing.activePopover}
                  editor={holdsTheEditor(group.items) ? editing.editor : null}
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
