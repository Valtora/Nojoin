"use client";

import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { memo } from "react";
import { Pause, Play } from "lucide-react";

import { getColorByKey } from "@/lib/constants";
import type { OutboxDraft } from "@/lib/notes/noteOutbox";
import { segmentSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import type { SpeakerNameDecision } from "@/lib/speakerNameReview";
import {
  GlobalSpeaker,
  RecordingSpeaker,
  TranscriptSegment,
  TranscriptSpeakerAssignment,
} from "@/types";

import IconButton from "../ui/IconButton";
import { TOUCH_TARGET_SQUARE } from "../ui/touchTarget";

import type {
  SpeakerPopover,
  TranscriptLineActions,
  TranscriptOpenEditor,
} from "./_hooks/useTranscriptEditing";
import { sameDecorations, type TextDecoration } from "./textDecorations";
import { formatTime } from "./transcriptDisplay";
import {
  LINE_KEY_SEPARATOR,
  sameGroup,
  type IndexedTranscriptSegment,
  type TranscriptGroup as TranscriptGroupData,
} from "./transcriptGroups";
import TranscriptLine from "./TranscriptLine";

/** What every line of the view shares; memoised by the view. */
export interface TranscriptLineShared {
  readOnly: boolean;
  allowProvisionalEdits: boolean;
  speakerMap: Record<string, string>;
  speakers: readonly RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  speakerColors: Record<string, string>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | Promise<void>;
  /** Confirms or dismisses a suggested speaker name; null where names can't be reviewed. */
  onReviewSpeakerName:
    | ((label: string, name: string, decision: SpeakerNameDecision) => Promise<void>)
    | null;
  pendingRemoteUtteranceIds: ReadonlySet<string>;
  actions: TranscriptLineActions;
  /** Queues a note on a line; null where notes can't be added. */
  onAddNote: ((note: OutboxDraft) => string | null) | null;
  /** Plays on from a line's start, in seconds; null where playback is off. */
  onPlayFrom: ((startSeconds: number) => void) | null;
}

/** "Alex", "Alex and Blair", "Alex, Blair, and Sam". */
const SPEAKER_LIST = new Intl.ListFormat("en", { type: "conjunction" });

interface TranscriptGroupProps {
  group: TranscriptGroupData;
  isGroupActive: boolean;
  /** Playback is running and inside this group: false for every other group. */
  isPlaying: boolean;
  onPlaySegment: (start: number, end: number) => void;
  onPause: () => void;
  onResume: () => void;
  disableSegmentPlayback: boolean;
  /** Where each speaker first appears among the shown lines. */
  speakerDisplayOrder: ReadonlyMap<string, number>;
  /** Each speaker's first shown line, by key. */
  speakerFirstLines: ReadonlyMap<string, string>;
  lineShared: TranscriptLineShared;
  /** Keys of this group's lines that playback is inside, one per line. */
  activeLineKeys: string;
  /** Keys of this group's lines revised in the last 15 s, one per line. */
  recentLineKeys: string;
  searchDecorations: (segmentId: string) => readonly TextDecoration[];
  /** Each line's marks of the words selection notes are on. */
  noteDecorations: (segmentId: string) => readonly TextDecoration[];
  /** The line whose speaker rename is open. */
  editingSpeakerLineId: string | null;
  editingTextId: string | null;
  activePopover: SpeakerPopover | null;
  /** The open editor when one of this group's lines shows it, else null. */
  editor: TranscriptOpenEditor | null;
}

/**
 * A run of lines that overlap in time, under one timestamp and play button.
 * When more than one speaker is involved, each gets a lane.
 *
 * Memoised, like its lines: the view hands it values and lists of keys that
 * change only when something in this group does, so a playback tick redraws
 * the groups it enters and leaves and no others.
 */
function TranscriptGroup({
  group,
  isGroupActive,
  isPlaying,
  onPlaySegment,
  onPause,
  onResume,
  disableSegmentPlayback,
  speakerDisplayOrder,
  speakerFirstLines,
  lineShared,
  activeLineKeys,
  recentLineKeys,
  searchDecorations,
  noteDecorations,
  editingSpeakerLineId,
  editingTextId,
  activePopover,
  editor,
}: TranscriptGroupProps) {
  /** The play button pauses this group's playback rather than starting it. */
  const pauses = isGroupActive && isPlaying;
  const { speakerMap, speakerColors } = lineShared;
  const activeKeys = activeLineKeys.split(LINE_KEY_SEPARATOR);
  const recentKeys = recentLineKeys.split(LINE_KEY_SEPARATOR);
  const groupHasRecentRevision = recentLineKeys !== "";

  const renderLine = (item: IndexedTranscriptSegment) => {
    const { segment, segmentId } = item;
    const isEditingSpeaker = editingSpeakerLineId === segmentId;
    const isEditingText = editingTextId === segmentId;
    return (
      <TranscriptLine
        key={segmentId}
        segment={segment}
        index={item.index}
        segmentId={segmentId}
        isActive={activeKeys.includes(segmentId)}
        readOnly={lineShared.readOnly}
        allowProvisionalEdits={lineShared.allowProvisionalEdits}
        speakerMap={speakerMap}
        speakers={lineShared.speakers}
        globalSpeakers={lineShared.globalSpeakers}
        speakerColors={speakerColors}
        onUpdateSegmentSpeaker={lineShared.onUpdateSegmentSpeaker}
        onReviewSpeakerName={lineShared.onReviewSpeakerName}
        isSpeakersFirstLine={speakerFirstLines.get(segment.speaker) === segmentId}
        hasPendingRemoteUpdate={
          typeof segment.id === "string" &&
          lineShared.pendingRemoteUtteranceIds.has(segment.id)
        }
        isRecentlyUpdated={recentKeys.includes(segmentId)}
        decorations={searchDecorations(segmentId)}
        noteDecorations={noteDecorations(segmentId)}
        isEditingSpeaker={isEditingSpeaker}
        isEditingText={isEditingText}
        popoverAnchor={activePopover?.segmentId === segmentId ? activePopover.anchor : null}
        actions={lineShared.actions}
        onAddNote={lineShared.onAddNote}
        onPlayFrom={lineShared.onPlayFrom}
        editor={isEditingSpeaker || isEditingText ? editor : null}
      />
    );
  };

  const involvedSpeakers = Array.from(group.involved).sort((left, right) => {
    const leftOrder = speakerDisplayOrder.get(left) ?? Number.MAX_SAFE_INTEGER;
    const rightOrder = speakerDisplayOrder.get(right) ?? Number.MAX_SAFE_INTEGER;

    if (leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }

    return segmentSpeakerDisplayName(speakerMap, left).localeCompare(
      segmentSpeakerDisplayName(speakerMap, right),
    );
  });

  return (
    <div
      className={`flex gap-3 px-2 group ${isGroupActive ? "opacity-100" : "opacity-90"} transition-opacity`}
    >
      {/* Timestamp & Play Control */}
      <div className="flex flex-col items-end min-w-16 md:min-w-[60px] pt-1 mt-1">
        <span className="text-sm text-contrast-icon-muted font-mono mb-1">
          {formatTime(group.start)}
        </span>
        {!disableSegmentPlayback && (
          <IconButton
            size="sm"
            // Who and when, so two groups in the same second differ.
            aria-label={`${pauses ? "Pause" : "Play"} ${SPEAKER_LIST.format(
              involvedSpeakers.map((speaker) =>
                segmentSpeakerDisplayName(speakerMap, speaker),
              ),
            )} at ${formatTime(group.start)}`}
            title={pauses ? "Pause segment" : "Play segment"}
            onClick={() => {
              if (isGroupActive) {
                if (isPlaying) onPause();
                else onResume();
              } else {
                onPlaySegment(group.start, group.end);
              }
            }}
            className={`rounded-full ${TOUCH_TARGET_SQUARE} ${
              isGroupActive
                ? "bg-status-success-bg text-foreground hover:bg-status-success-bg hover:text-foreground"
                : "bg-surface-inset text-contrast-helper hover:bg-action hover:text-action-on"
            }`}
            icon={
              pauses ? (
                <Pause aria-hidden="true" className="fill-current" />
              ) : (
                <Play aria-hidden="true" className="fill-current" />
              )
            }
          />
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {involvedSpeakers.length > 1 ? (
          <div className="grid gap-3 md:gap-4 w-full border border-surface-border bg-surface-inset p-3 md:p-4 rounded-xl  md:grid-cols-[repeat(auto-fit,minmax(0,1fr))]">
            {involvedSpeakers.map((speaker) => {
              const speakerItems = group.items.filter(
                (item) => item.segment.speaker === speaker,
              );
              const laneColor = getColorByKey(speakerColors[speaker] || "gray");
              const laneMinHeightRem = groupHasRecentRevision
                ? Math.max(7, speakerItems.length * 4.5)
                : undefined;
              return (
                <SurfaceBase kind="card"
                  key={speaker}
                  data-testid={`overlap-lane-${speaker}`}
                  className={`min-w-0 flex flex-col rounded-xl border ${laneColor.border} bg-surface-card`}
                >
                  <div className={`px-3 py-2 text-xs font-semibold uppercase tracking-[0.16em] ${laneColor.text} border-b border-surface-divider`}>
                    {segmentSpeakerDisplayName(speakerMap, speaker)}
                  </div>
                  <div
                    data-testid={`overlap-lane-body-${speaker}`}
                    className="p-3 flex-1 min-w-0"
                    style={laneMinHeightRem ? { minHeight: `${laneMinHeightRem}rem` } : undefined}
                  >
                    {speakerItems.length > 0 ? (
                      speakerItems.map(renderLine)
                    ) : (
                      <SurfaceBase kind="card" className="h-full w-full min-h-[50px] flex items-center justify-center border border-dashed border-control-border rounded-xl bg-surface-card">
                        <div className="flex flex-col items-center gap-0.5 opacity-60">
                          <div className="font-semibold text-contrast-helper text-sm">{segmentSpeakerDisplayName(speakerMap, speaker)}</div>
                          <div className="text-[11px] uppercase tracking-wider">(overlapping speech)</div>
                        </div>
                      </SurfaceBase>
                    )}
                  </div>
                </SurfaceBase>
              );
            })}
          </div>
        ) : (
          <div className="flex flex-col w-full">
            {group.items.map(renderLine)}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Every prop by identity, except four that are rebuilt more often than they
 * change: the group, which grouping makes anew whenever any line changes; the
 * speaker order, which only matters for this group's speakers; and the search
 * and note decorations, functions that are new whenever any line's marks
 * change. Those compare by what this group would draw from them.
 */
function sameGroupProps(previous: TranscriptGroupProps, next: TranscriptGroupProps): boolean {
  const keys = Object.keys(next) as (keyof TranscriptGroupProps)[];
  return (
    keys.length === Object.keys(previous).length &&
    keys.every((key) => {
      switch (key) {
        case "group":
          return sameGroup(previous.group, next.group);
        case "speakerDisplayOrder":
          return [...next.group.involved].every(
            (speaker) =>
              previous.speakerDisplayOrder.get(speaker) === next.speakerDisplayOrder.get(speaker),
          );
        case "speakerFirstLines":
          return next.group.items.every(
            ({ segment, segmentId }) =>
              (previous.speakerFirstLines.get(segment.speaker) === segmentId) ===
              (next.speakerFirstLines.get(segment.speaker) === segmentId),
          );
        case "searchDecorations":
        case "noteDecorations":
          return next.group.items.every(({ segmentId }) =>
            sameDecorations(previous[key](segmentId), next[key](segmentId)),
          );
        default:
          return Object.is(previous[key], next[key]);
      }
    })
  );
}

export default memo(TranscriptGroup, sameGroupProps);
