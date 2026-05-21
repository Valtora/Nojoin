import { TranscriptSegment, TranscriptUtterance } from "@/types";

export interface TranscriptSegmentChange {
  before: TranscriptSegment;
  after: TranscriptSegment;
  changedFields: Array<"text" | "speaker">;
}

export const getTranscriptSegmentKey = (
  segment: Pick<TranscriptSegment, "id" | "start" | "end" | "speaker">,
  fallbackIndex = 0,
): string => {
  if (segment.id) {
    return segment.id;
  }

  return `legacy-${fallbackIndex}-${segment.start}-${segment.end}-${segment.speaker}`;
};

export const sortTranscriptSegments = (
  segments: TranscriptSegment[],
): TranscriptSegment[] => {
  return [...segments].sort((left, right) => {
    if (left.start !== right.start) {
      return left.start - right.start;
    }

    if (left.end !== right.end) {
      return left.end - right.end;
    }

    return (left.id || "").localeCompare(right.id || "");
  });
};

export const transcriptSegmentFromUtterance = (
  utterance: TranscriptUtterance,
): TranscriptSegment => {
  return {
    id: utterance.id,
    start: utterance.start,
    end: utterance.end,
    text: utterance.text,
    speaker: utterance.speaker,
    recording_speaker_id: utterance.recording_speaker_id,
    state: utterance.state,
    revision: utterance.revision,
    speaker_state: utterance.speaker_state || utterance.state,
    overlapping_speakers: utterance.overlapping_speakers,
    provisional: utterance.provisional,
    segment_source: utterance.segment_source,
    speaker_manually_edited: utterance.speaker_manually_edited,
    text_manually_edited: utterance.text_manually_edited,
    speaker_confidence: utterance.speaker_confidence,
    text_confidence: utterance.text_confidence,
    updated_at: utterance.updated_at,
  };
};

export const mergeTranscriptUtteranceDelta = (
  currentSegments: TranscriptSegment[],
  utterances: TranscriptUtterance[],
  tombstones: string[],
): TranscriptSegment[] => {
  const legacySegments: TranscriptSegment[] = [];
  const segmentsById = new Map<string, TranscriptSegment>();

  currentSegments.forEach((segment) => {
    if (!segment.id) {
      legacySegments.push(segment);
      return;
    }

    segmentsById.set(segment.id, segment);
  });

  tombstones.forEach((utteranceId) => {
    segmentsById.delete(utteranceId);
  });

  utterances.forEach((utterance) => {
    segmentsById.set(utterance.id, transcriptSegmentFromUtterance(utterance));
  });

  return sortTranscriptSegments([...legacySegments, ...segmentsById.values()]);
};

export const diffTranscriptSegments = (
  previousSegments: TranscriptSegment[],
  nextSegments: TranscriptSegment[],
): TranscriptSegmentChange[] => {
  const nextById = new Map(
    nextSegments
      .filter((segment): segment is TranscriptSegment & { id: string } =>
        Boolean(segment.id),
      )
      .map((segment) => [segment.id, segment]),
  );

  return previousSegments
    .filter((segment): segment is TranscriptSegment & { id: string } =>
      Boolean(segment.id),
    )
    .flatMap((segment) => {
      const nextSegment = nextById.get(segment.id);
      if (!nextSegment) {
        return [];
      }

      const changedFields: Array<"text" | "speaker"> = [];

      if (segment.text !== nextSegment.text) {
        changedFields.push("text");
      }

      if (segment.speaker !== nextSegment.speaker) {
        changedFields.push("speaker");
      }

      if (changedFields.length === 0) {
        return [];
      }

      return [
        {
          before: segment,
          after: nextSegment,
          changedFields,
        },
      ];
    });
};