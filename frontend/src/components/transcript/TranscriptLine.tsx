"use client";

import { memo } from "react";

import { getColorByKey } from "@/lib/constants";
import { segmentEndMs, segmentStartMs } from "@/lib/notes/placement";
import {
  GlobalSpeaker,
  RecordingSpeaker,
  TranscriptSegment,
  TranscriptSpeakerAssignment,
} from "@/types";

import SpeakerAssignmentPopover from "../SpeakerAssignmentPopover";

import type {
  TranscriptLineActions,
  TranscriptOpenEditor,
} from "./_hooks/useTranscriptEditing";
import type { TextDecoration } from "./textDecorations";
import type { IndexedTranscriptSegment } from "./transcriptGroups";
import TranscriptLineBadges from "./TranscriptLineBadges";
import TranscriptLineText from "./TranscriptLineText";

/**
 * The line, its place among all the lines and its key, as separate props:
 * grouping wraps them anew whenever any line changes, and a line whose own
 * segment object is unchanged need not redraw.
 */
type TranscriptLineProps = Pick<IndexedTranscriptSegment, "segment" | "index" | "segmentId"> & {
  /** Playback is inside this line. */
  isActive: boolean;
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
  hasPendingRemoteUpdate: boolean;
  /** Revised in the last 15 s, as of the view's last render. */
  isRecentlyUpdated: boolean;
  decorations: readonly TextDecoration[];
  /** This line's speaker is being renamed. */
  isEditingSpeaker: boolean;
  /** This line's text is open in the editor. */
  isEditingText: boolean;
  /** Where this line's speaker popover sits, while it is open. */
  popoverAnchor: HTMLElement | null;
  actions: TranscriptLineActions;
  /** The open editor, for a line that shows it; null on every other line. */
  editor: TranscriptOpenEditor | null;
};

/**
 * One line of the finished transcript: its speaker (click to reassign,
 * double-click to rename), its pills, and its text in a bubble that turns into
 * an editor on click.
 *
 * Memoised: a playback tick or a keystroke in the find bar redraws only the
 * lines whose own props changed, so every prop is either a value or keeps its
 * identity until it means something new.
 */
function TranscriptLine({
  segment,
  index,
  segmentId,
  isActive,
  readOnly,
  allowProvisionalEdits,
  speakerMap,
  speakers,
  globalSpeakers,
  speakerColors,
  onUpdateSegmentSpeaker,
  hasPendingRemoteUpdate,
  isRecentlyUpdated,
  decorations,
  isEditingSpeaker,
  isEditingText,
  popoverAnchor,
  actions,
  editor,
}: TranscriptLineProps) {
  const isProvisional = segment.provisional === true;
  const isSegmentReadOnly = readOnly || (isProvisional && !allowProvisionalEdits);
  const speakerName = speakerMap[segment.speaker] || segment.speaker;
  const speakerColor = getColorByKey(speakerColors[segment.speaker] || "gray");
  const bubbleColor = isActive
    ? "border-2 border-status-success-border bg-status-success-bg"
    : `${speakerColor.bg} ${speakerColor.border}`;

  return (
    <div
      data-segment-id={segmentId}
      data-order-index={index}
      className="flex flex-col mb-3 last:mb-0"
    >
      {/* Speaker Label */}
      <div className="flex flex-wrap items-baseline gap-2 mb-1">
        {isEditingSpeaker && editor ? (
          <input
            autoFocus
            type="text"
            value={editor.value}
            onChange={(e) => editor.setValue(e.target.value)}
            onBlur={editor.handleSpeakerRenameSubmit}
            onKeyDown={(e) => {
              if (e.key === "Enter") editor.handleSpeakerRenameSubmit();
              if (e.key === "Escape") editor.cancelSpeakerRename();
            }}
            onClick={(e) => e.stopPropagation()}
            className="text-sm font-bold text-status-info-fg bg-surface-card border border-status-info-border rounded px-1 py-0.5 focus:outline-none focus:ring-2 focus:ring-status-info-border"
          />
        ) : (
          <div className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (isSegmentReadOnly) return;
                actions.toggleSpeakerPopover(segmentId, e.currentTarget);
              }}
              onDoubleClick={(e) => {
                e.stopPropagation();
                if (isSegmentReadOnly) return;
                actions.startSpeakerRename(segment.speaker, speakerName);
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
                  : "Click to change speaker, Double-click to rename"
              }
            >
              {speakerName}
            </button>

            {popoverAnchor && !isSegmentReadOnly && (
              <SpeakerAssignmentPopover
                availableSpeakers={speakers}
                globalSpeakers={globalSpeakers}
                currentSpeakerLabel={segment.speaker}
                speakerColors={speakerColors}
                targetElement={popoverAnchor}
                onSelect={(assignment) => {
                  onUpdateSegmentSpeaker(segment, assignment);
                  actions.closeSpeakerPopover();
                }}
                onClose={actions.closeSpeakerPopover}
              />
            )}
          </div>
        )}

        <TranscriptLineBadges
          segment={segment}
          showRevised={isRecentlyUpdated && !isEditingText}
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
        {isEditingText && editor ? (
          <textarea
            autoFocus
            value={editor.value}
            onChange={(e) => editor.setValue(e.target.value)}
            onBlur={() => editor.handleTextSubmit(segment)}
            onKeyDown={(e) => editor.handleKeyDown(e, segment)}
            className="w-full bg-transparent resize-none outline-none text-foreground leading-relaxed"
            rows={Math.max(2, Math.ceil(editor.value.length / 80))}
          />
        ) : (
          <TranscriptLineText
            text={segment.text}
            segmentId={segmentId}
            startMs={segmentStartMs(segment)}
            endMs={segmentEndMs(segment)}
            decorations={decorations}
            className={`leading-relaxed whitespace-pre-wrap text-contrast-muted ${
              isSegmentReadOnly
                ? ""
                : "cursor-text hover:text-foreground"
            }`}
            onClick={(e) => {
              e.stopPropagation();
              actions.startTextEdit(segment, segmentId);
            }}
            title={isSegmentReadOnly ? undefined : "Click to edit text"}
          />
        )}
      </div>
    </div>
  );
}

export default memo(TranscriptLine);
