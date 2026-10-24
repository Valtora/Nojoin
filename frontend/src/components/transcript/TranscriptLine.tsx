"use client";

import { memo, useMemo, useRef } from "react";

import { getColorByKey } from "@/lib/constants";
import type { OutboxDraft } from "@/lib/notes/noteOutbox";
import { segmentEndMs, segmentStartMs } from "@/lib/notes/placement";
import {
  liveSpeakerDisplayName,
  segmentSpeakerDisplayName,
} from "@/lib/recordingSpeakerUtils";
import {
  describeSuggestedNameSource,
  type SpeakerNameDecision,
} from "@/lib/speakerNameReview";
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
import SpeakerNameReviewPopover from "./SpeakerNameReviewPopover";
import TranscriptLineBadges from "./TranscriptLineBadges";
import TranscriptLineMenu from "./TranscriptLineMenu";
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
  /** Confirms or dismisses a suggested speaker name; null where names can't be reviewed. */
  onReviewSpeakerName:
    | ((label: string, name: string, decision: SpeakerNameDecision) => Promise<void>)
    | null;
  /**
   * The first line of its speaker among those shown, the one that offers a
   * suggested name's "?": once per speaker, not on every line.
   */
  isSpeakersFirstLine: boolean;
  hasPendingRemoteUpdate: boolean;
  /** Revised in the last 15 s, as of the view's last render. */
  isRecentlyUpdated: boolean;
  decorations: readonly TextDecoration[];
  /** The marks of the words selection notes are on. */
  noteDecorations: readonly TextDecoration[];
  /** This line's speaker is being renamed. */
  isEditingSpeaker: boolean;
  /** This line's text is open in the editor. */
  isEditingText: boolean;
  /** Where this line's speaker popover sits, while it is open. */
  popoverAnchor: HTMLElement | null;
  actions: TranscriptLineActions;
  onAddNote: ((note: OutboxDraft) => string | null) | null;
  onPlayFrom: ((startSeconds: number) => void) | null;
  /** The open editor, for a line that shows it; null on every other line. */
  editor: TranscriptOpenEditor | null;
};

/**
 * One line of the finished transcript: its speaker (click to reassign,
 * double-click to rename), its pills, its ⋯ menu, and its text in a bubble,
 * which turns into an editor only from the menu's Edit text: a click on the
 * text selects it, for a note or a copy.
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
  onReviewSpeakerName,
  isSpeakersFirstLine,
  hasPendingRemoteUpdate,
  isRecentlyUpdated,
  decorations,
  noteDecorations,
  isEditingSpeaker,
  isEditingText,
  popoverAnchor,
  actions,
  onAddNote,
  onPlayFrom,
  editor,
}: TranscriptLineProps) {
  const textDecorations = useMemo(
    () =>
      noteDecorations.length === 0
        ? decorations
        : decorations.length === 0
          ? noteDecorations
          : [...decorations, ...noteDecorations],
    [decorations, noteDecorations],
  );
  const isProvisional = segment.provisional === true;
  const isSegmentReadOnly = readOnly || (isProvisional && !allowProvisionalEdits);
  const speakerName = segmentSpeakerDisplayName(speakerMap, segment.speaker);
  // A live label is no speaker of the recording yet, so it has no name to
  // change; the line can still be assigned to a speaker.
  const canRenameSpeaker = liveSpeakerDisplayName(segment.speaker) === null;
  // The line's speaker, while its name is a suggestion nobody has confirmed.
  const pendingSpeaker = useMemo(
    () =>
      speakers.find(
        (speaker) =>
          speaker.diarization_label === segment.speaker &&
          !speaker.merged_into_id &&
          speaker.name_pending === true,
      ) ?? null,
    [speakers, segment.speaker],
  );
  // A provisional line's speaker may yet change, so its name is reviewed
  // once the line is final.
  const canReviewName =
    isSpeakersFirstLine && !isSegmentReadOnly && !isProvisional && !isEditingSpeaker;
  // A decision drops the "?", so focus goes to the speaker's name beside it.
  const speakerButtonRef = useRef<HTMLButtonElement>(null);
  const speakerColor = getColorByKey(speakerColors[segment.speaker] || "gray");
  const bubbleColor = isActive
    ? "border-2 border-status-success-border bg-status-success-bg"
    : `${speakerColor.bg} ${speakerColor.border}`;

  return (
    <div
      data-segment-id={segmentId}
      data-order-index={index}
      data-line-actions
      className="group/line flex flex-col mb-3 last:mb-0"
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
              ref={speakerButtonRef}
              onClick={(e) => {
                e.stopPropagation();
                if (isSegmentReadOnly) return;
                actions.toggleSpeakerPopover(segmentId, e.currentTarget);
              }}
              onDoubleClick={(e) => {
                e.stopPropagation();
                if (isSegmentReadOnly || !canRenameSpeaker) return;
                actions.startSpeakerRename(segmentId, segment.speaker, speakerName);
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

        {pendingSpeaker && onReviewSpeakerName && canReviewName && (
          <SpeakerNameReviewPopover
            label={segment.speaker}
            name={speakerName}
            reason={describeSuggestedNameSource(pendingSpeaker)}
            onReview={onReviewSpeakerName}
            focusAfterDecision={() => speakerButtonRef.current?.focus()}
          />
        )}

        <TranscriptLineBadges
          segment={segment}
          showRevised={isRecentlyUpdated && !isEditingText}
          hasPendingRemoteUpdate={hasPendingRemoteUpdate}
        />

        <TranscriptLineMenu
          segment={segment}
          segmentId={segmentId}
          speakerName={speakerName}
          isSegmentReadOnly={isSegmentReadOnly}
          actions={actions}
          onAddNote={onAddNote}
          onPlayFrom={onPlayFrom}
          isEditingText={isEditingText}
          isChangingSpeaker={popoverAnchor !== null}
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
            decorations={textDecorations}
            className="leading-relaxed whitespace-pre-wrap text-contrast-muted"
          />
        )}
      </div>
    </div>
  );
}

export default memo(TranscriptLine);
