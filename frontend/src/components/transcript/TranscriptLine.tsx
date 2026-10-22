"use client";

import type { RefObject } from "react";

import { getColorByKey } from "@/lib/constants";
import { segmentEndMs, segmentStartMs } from "@/lib/notes/placement";
import {
  liveSpeakerDisplayName,
  segmentSpeakerDisplayName,
} from "@/lib/recordingSpeakerUtils";
import {
  GlobalSpeaker,
  RecordingSpeaker,
  TranscriptSegment,
  TranscriptSpeakerAssignment,
} from "@/types";

import SpeakerAssignmentPopover from "../SpeakerAssignmentPopover";

import type { TranscriptEditing } from "./_hooks/useTranscriptEditing";
import type { TextDecoration } from "./textDecorations";
import { isRecentlyUpdatedSegment } from "./transcriptDisplay";
import type { IndexedTranscriptSegment } from "./transcriptGroups";
import TranscriptLineBadges from "./TranscriptLineBadges";
import TranscriptLineText from "./TranscriptLineText";

interface TranscriptLineProps {
  item: IndexedTranscriptSegment;
  currentTime: number;
  activeSegmentRef: RefObject<HTMLDivElement | null>;
  readOnly: boolean;
  allowProvisionalEdits: boolean;
  speakerMap: Record<string, string>;
  speakers: RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  speakerColors: Record<string, string>;
  onUpdateSegmentSpeaker: (
    segment: TranscriptSegment,
    assignment: TranscriptSpeakerAssignment,
  ) => void | Promise<void>;
  hasPendingRemoteUpdate: boolean;
  decorations: readonly TextDecoration[];
  editing: TranscriptEditing;
}

/**
 * One line of the finished transcript: its speaker (click to reassign,
 * double-click to rename), its pills, and its text in a bubble that turns into
 * an editor on click.
 */
export default function TranscriptLine({
  item,
  currentTime,
  activeSegmentRef,
  readOnly,
  allowProvisionalEdits,
  speakerMap,
  speakers,
  globalSpeakers,
  speakerColors,
  onUpdateSegmentSpeaker,
  hasPendingRemoteUpdate,
  decorations,
  editing,
}: TranscriptLineProps) {
  const { segment, segmentId } = item;
  const {
    editingSpeaker,
    editingSegmentSpeakerId,
    editingTextId,
    activePopover,
    editValue,
    setEditValue,
  } = editing;
  const isActive = currentTime >= segment.start && currentTime < segment.end;
  const isProvisional = segment.provisional === true;
  const isSegmentReadOnly = readOnly || (isProvisional && !allowProvisionalEdits);
  const speakerName = segmentSpeakerDisplayName(speakerMap, segment.speaker);
  // A live label is no speaker of the recording yet, so it has no name to
  // change; the line can still be assigned to a speaker.
  const canRenameSpeaker = liveSpeakerDisplayName(segment.speaker) === null;
  const isEditingSpeaker = editingSpeaker === segment.speaker;
  const isEditingSegmentSpeaker = editingSegmentSpeakerId === segmentId;
  const isEditingText = editingTextId === segmentId;
  const isRecentlyUpdated = isRecentlyUpdatedSegment(segment);
  const speakerColor = getColorByKey(speakerColors[segment.speaker] || "gray");
  const bubbleColor = isActive
    ? "border-2 border-status-success-border bg-status-success-bg"
    : `${speakerColor.bg} ${speakerColor.border}`;

  return (
    <div
      ref={isActive ? activeSegmentRef : null}
      data-segment-id={segmentId}
      data-order-index={item.index}
      className="flex flex-col mb-3 last:mb-0"
    >
      {/* Speaker Label */}
      <div className="flex flex-wrap items-baseline gap-2 mb-1">
        {isEditingSpeaker ? (
          <input
            autoFocus
            type="text"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={editing.handleSpeakerRenameSubmit}
            onKeyDown={(e) => {
              if (e.key === "Enter") editing.handleSpeakerRenameSubmit();
              if (e.key === "Escape") editing.cancelSpeakerRename();
            }}
            onClick={(e) => e.stopPropagation()}
            className="text-sm font-bold text-status-info-fg bg-surface-card border border-status-info-border rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-status-info-border"
          />
        ) : isEditingSegmentSpeaker ? (
          <input
            autoFocus
            type="text"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={() => editing.handleSegmentSpeakerSubmit(segment)}
            onKeyDown={(e) => editing.handleKeyDown(e, "segmentSpeaker", segment)}
            onClick={(e) => e.stopPropagation()}
            className="text-sm font-bold text-status-success-fg bg-surface-card border border-status-success-border rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-status-success-border"
          />
        ) : (
          <div className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (isSegmentReadOnly) return;
                editing.toggleSpeakerPopover(segmentId, e.currentTarget);
              }}
              onDoubleClick={(e) => {
                e.stopPropagation();
                if (isSegmentReadOnly || !canRenameSpeaker) return;
                editing.startSpeakerRename(segment.speaker, speakerName);
              }}
              disabled={isSegmentReadOnly}
              className={`text-base font-bold transition-colors text-left ${
                isProvisional ? speakerColor.text : "text-contrast-muted"
              } ${
                isSegmentReadOnly
                  ? "cursor-default"
                  : "hover:text-action-text"
              }`}
              title={
                isSegmentReadOnly
                  ? speakerName
                  : canRenameSpeaker
                    ? "Click to change speaker, Double-click to rename"
                    : "Click to change speaker"
              }
            >
              {speakerName}
            </button>

            {activePopover?.segmentId === segmentId && !isSegmentReadOnly && (
              <SpeakerAssignmentPopover
                availableSpeakers={speakers}
                globalSpeakers={globalSpeakers}
                currentSpeakerLabel={segment.speaker}
                speakerColors={speakerColors}
                targetElement={activePopover.target}
                onSelect={(assignment) => {
                  onUpdateSegmentSpeaker(segment, assignment);
                  editing.closeSpeakerPopover();
                }}
                onClose={editing.closeSpeakerPopover}
              />
            )}
          </div>
        )}

        <TranscriptLineBadges
          segment={segment}
          showRevised={isRecentlyUpdated && !isEditingText && !isEditingSegmentSpeaker}
          hasPendingRemoteUpdate={hasPendingRemoteUpdate}
        />
      </div>

      {/* Transcript Text */}
      <div
        id={`segment-${segmentId}`}
        className={`p-3 rounded-2xl rounded-tl-none w-full transition-colors border ${bubbleColor} ${
          isEditingText ? "ring-2 ring-status-info-border" : ""
        } ${
          isProvisional
            ? "border-dashed border-2 shadow-card"
            : ""
        }`}
      >
        {isEditingText ? (
          <textarea
            autoFocus
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={() => editing.handleTextSubmit(segment)}
            onKeyDown={(e) => editing.handleKeyDown(e, "text", segment)}
            className="w-full bg-transparent resize-none outline-none text-foreground leading-relaxed"
            rows={Math.max(2, Math.ceil(editValue.length / 80))}
          />
        ) : (
          <TranscriptLineText
            text={segment.text}
            segmentKey={segmentId}
            startMs={segmentStartMs(segment)}
            endMs={segmentEndMs(segment)}
            decorations={decorations}
            className={`leading-relaxed whitespace-pre-wrap text-contrast-muted ${
              isSegmentReadOnly
                ? ""
                : "cursor-text hover:text-foreground"
            }`}
            onClick={(e) => editing.handleTextClick(segment, segmentId, e)}
            title={isSegmentReadOnly ? undefined : "Click to edit text"}
          />
        )}
      </div>
    </div>
  );
}
