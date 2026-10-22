"use client";

import { Sparkles } from "lucide-react";

import { TranscriptSegment } from "@/types";

interface TranscriptLineBadgesProps {
  segment: TranscriptSegment;
  /** Revised recently, and not open in an editor. */
  showRevised: boolean;
  hasPendingRemoteUpdate: boolean;
}

/** The pills beside a finished line's speaker: edits, confidence, revisions. */
export default function TranscriptLineBadges({
  segment,
  showRevised,
  hasPendingRemoteUpdate,
}: TranscriptLineBadgesProps) {
  const isSpeakerLowConfidence =
    typeof segment.speaker_confidence === "number" &&
    segment.speaker_confidence < 0.6 &&
    !segment.speaker_manually_edited;
  // An assistant editing someone's transcript is disclosed; a person editing
  // their own is not remarkable, so only the assistant side names the field.
  // Anything edited without a recorded source stays the neutral pill rather
  // than being attributed to a person the backend cannot vouch for.
  const isAiTextEdit =
    segment.text_manually_edited === true && segment.text_edit_source === "mcp";
  const isAiSpeakerEdit =
    segment.speaker_manually_edited === true &&
    segment.speaker_edit_source === "mcp";

  return (
    <div className="flex flex-wrap items-center gap-1">
      {isAiTextEdit ? (
        <span
          className="inline-flex items-center gap-1 rounded-pill border border-surface-border bg-surface-inset px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-foreground"
          title="An assistant connected through the MCP connector corrected this line's text"
        >
          <Sparkles className="h-2.5 w-2.5" aria-hidden="true" />
          AI corrected text
        </span>
      ) : (
        segment.text_manually_edited && (
          <span className="rounded-pill border border-surface-border bg-surface-inset px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-contrast-muted">
            Edited
          </span>
        )
      )}
      {isAiSpeakerEdit && (
        <span
          className="inline-flex items-center gap-1 rounded-pill border border-surface-border bg-surface-inset px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-foreground"
          title="An assistant connected through the MCP connector corrected which speaker this line is attributed to"
        >
          <Sparkles className="h-2.5 w-2.5" aria-hidden="true" />
          AI corrected speaker
        </span>
      )}
      {isSpeakerLowConfidence && (
        <span className="rounded-pill border border-status-danger-border bg-status-danger-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-danger-fg">
          Low confidence
        </span>
      )}
      {showRevised && (
        <span className="rounded-pill border border-status-success-border bg-status-success-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-success-fg">
          Revised
        </span>
      )}
      {hasPendingRemoteUpdate && (
        <span className="rounded-pill border border-status-info-border bg-status-info-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-info-fg">
          Pending update
        </span>
      )}
    </div>
  );
}
