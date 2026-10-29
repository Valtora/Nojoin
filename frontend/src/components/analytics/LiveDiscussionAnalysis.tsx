"use client";

import { useMemo } from "react";

import GenerationCoverage from "@/components/GenerationCoverage";
import type { AnalyticsSpeaker, RecordingId } from "@/types";
import MeetingAnalysisPanel from "./MeetingAnalysisPanel";
import { buildSpeakerColors } from "./speakerPalette";
import { useDiscussionAnalysis } from "./useDiscussionAnalysis";

/** Discussion uses its own snapshot, request and errors while live estimates continue. */
export default function LiveDiscussionAnalysis({ recordingId, speakers, speakerColors, onPlaySegment }: {
  recordingId: RecordingId;
  speakers: AnalyticsSpeaker[];
  speakerColors: Record<string, string>;
  onPlaySegment?: (startMs: number) => void;
}) {
  const { analysis, readError, actionError, generate, generating } = useDiscussionAnalysis(recordingId);
  const colors = useMemo(() => buildSpeakerColors(speakers, speakerColors), [speakers, speakerColors]);

  return <div className="space-y-2">
    {readError && <p role="status" className="text-xs text-contrast-helper">{readError}</p>}
    {actionError && <p role="alert" className="text-sm text-status-danger-fg">{actionError}</p>}
    {!analysis ? <p className="text-sm text-contrast-helper">Loading discussion analysis...</p> : <>
      <GenerationCoverage metadata={analysis.ai ? { generated_through_ms: analysis.ai.analysed_through_ms } : null} />
      <MeetingAnalysisPanel ai={analysis.ai} status={analysis.ai_status}
        errorMessage={analysis.ai_error_message} stale={analysis.ai_stale} budget={analysis.ai_budget}
        speakers={speakers} colors={colors} onGenerate={() => void generate()}
        generating={generating} allowRegenerate onPlaySegment={onPlaySegment} />
    </>}
  </div>;
}
