"use client";

import type { ReactNode } from "react";
import { Pause, Play } from "lucide-react";

import { getColorByKey } from "@/lib/constants";

import IconButton from "../ui/IconButton";

import { formatTime, isRecentlyUpdatedSegment } from "./transcriptDisplay";
import type {
  IndexedTranscriptSegment,
  TranscriptGroup as TranscriptGroupData,
} from "./transcriptGroups";

interface TranscriptGroupProps {
  group: TranscriptGroupData;
  currentTime: number;
  isPlaying: boolean;
  onPlaySegment: (start: number, end: number) => void;
  onPause: () => void;
  onResume: () => void;
  disableSegmentPlayback: boolean;
  /** Where each speaker first appears among the shown lines. */
  speakerDisplayOrder: ReadonlyMap<string, number>;
  speakerMap: Record<string, string>;
  speakerColors: Record<string, string>;
  renderLine: (item: IndexedTranscriptSegment) => ReactNode;
}

/**
 * A run of lines that overlap in time, under one timestamp and play button.
 * When more than one speaker is involved, each gets a lane.
 */
export default function TranscriptGroup({
  group,
  currentTime,
  isPlaying,
  onPlaySegment,
  onPause,
  onResume,
  disableSegmentPlayback,
  speakerDisplayOrder,
  speakerMap,
  speakerColors,
  renderLine,
}: TranscriptGroupProps) {
  const isGroupActive =
    currentTime >= group.start && currentTime < group.end;
  /** The play button pauses this group's playback rather than starting it. */
  const pauses = isGroupActive && isPlaying;
  const groupHasRecentRevision = group.items.some(({ segment }) =>
    isRecentlyUpdatedSegment(segment),
  );

  const involvedSpeakers = Array.from(group.involved).sort((left, right) => {
    const leftOrder = speakerDisplayOrder.get(left) ?? Number.MAX_SAFE_INTEGER;
    const rightOrder = speakerDisplayOrder.get(right) ?? Number.MAX_SAFE_INTEGER;

    if (leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }

    return (speakerMap[left] || left).localeCompare(speakerMap[right] || right);
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
            aria-label={`${pauses ? "Pause" : "Play"} segment at ${formatTime(group.start)}`}
            title={pauses ? "Pause segment" : "Play segment"}
            onClick={() => {
              if (isGroupActive) {
                if (isPlaying) onPause();
                else onResume();
              } else {
                onPlaySegment(group.start, group.end);
              }
            }}
            className={`rounded-full shadow-card ${
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
                <div
                  key={speaker}
                  data-testid={`overlap-lane-${speaker}`}
                  className={`min-w-0 flex flex-col rounded-xl border ${laneColor.border} bg-surface-card`}
                >
                  <div className={`px-3 py-2 text-xs font-semibold uppercase tracking-[0.16em] ${laneColor.text} border-b border-surface-divider`}>
                    {speakerMap[speaker] || speaker}
                  </div>
                  <div
                    data-testid={`overlap-lane-body-${speaker}`}
                    className="p-3 flex-1 min-w-0"
                    style={laneMinHeightRem ? { minHeight: `${laneMinHeightRem}rem` } : undefined}
                  >
                    {speakerItems.length > 0 ? (
                      speakerItems.map(renderLine)
                    ) : (
                      <div className="h-full w-full min-h-[50px] flex items-center justify-center border border-dashed border-control-border rounded-xl bg-surface-card">
                        <div className="flex flex-col items-center gap-0.5 opacity-60">
                          <div className="font-semibold text-contrast-helper text-sm">{speakerMap[speaker] || speaker}</div>
                          <div className="text-[11px] uppercase tracking-wider">(overlapping speech)</div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
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
