"use client";

import { segmentEndMs, segmentStartMs } from "@/lib/notes/placement";
import { segmentSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import { lineIsSettling } from "@/lib/transcript/settling";
import type { TranscriptSegment } from "@/types";
import type { TextDecoration } from "./textDecorations";
import type { TranscriptLineShared } from "./TranscriptGroup";
import type { IndexedTranscriptSegment } from "./transcriptGroups";
import { liveRenderIdentity } from "./liveTranscriptPresentation";
import TranscriptLineMenu from "./TranscriptLineMenu";
import TranscriptLineText from "./TranscriptLineText";

export interface PendingContinuation {
  item: IndexedTranscriptSegment;
  decorations: readonly TextDecoration[];
  noteDecorations: readonly TextDecoration[];
}

/**
 * An original pending source line inside its predecessor's bubble. Its paragraph
 * keeps exactly its own text and word offsets; the separating space stays outside
 * it. Note actions and navigation likewise retain the pending line's own target.
 */
export default function PendingTranscriptContinuation({
  item: { segment, index, segmentId }, decorations, noteDecorations, previous, lineShared,
}: PendingContinuation & { previous: TranscriptSegment; lineShared: TranscriptLineShared }) {
  const separator = /\s$/.test(previous.text) || /^\s/.test(segment.text) || segment.words?.[0]?.join_left ? "" : " ";
  const joinsPrevious = separator === "" && !/\s$/.test(previous.text) && !/^\s/.test(segment.text);
  return <>
    {separator}
    <div id={`segment-${segmentId}`} data-segment-id={segmentId} data-order-index={index}
      data-render-identity={liveRenderIdentity(segment, index)} data-line-actions className="inline">
      <TranscriptLineText text={segment.text} segmentId={segmentId}
        startMs={segmentStartMs(segment)} endMs={segmentEndMs(segment)} revision={segment.revision}
        settlingRequired={segment.settling_required} settling={lineIsSettling(segment)} words={segment.words}
        joinsPrevious={joinsPrevious}
        decorations={[...decorations, ...noteDecorations]}
        className={`inline leading-relaxed whitespace-pre-wrap ${!segment.settling_required
          ? "italic text-contrast-helper" : "text-contrast-muted"}`} />
      <span className="inline-flex align-middle">
        <TranscriptLineMenu segment={segment} segmentId={segmentId}
          speakerName={segmentSpeakerDisplayName(lineShared.speakerMap, segment.speaker)} isSegmentReadOnly
          actions={lineShared.actions} onAddNote={lineShared.onAddNote} onPlayFrom={lineShared.onPlayFrom}
          readOnly={lineShared.readOnly} isEditingText={false} isChangingSpeaker={false}
          live={lineShared.live} speakers={lineShared.speakers} index={index}
          onLiveInteractionChange={lineShared.onLiveInteractionChange}
          renderIdentity={liveRenderIdentity(segment, index)} />
      </span>
    </div>
  </>;
}
