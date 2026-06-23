import {
  RecordingId,
  TranscriptSegment,
  TranscriptUtteranceList,
} from "@/types";

import {
  mergeTranscriptUtteranceDelta,
  sortTranscriptSegments,
  transcriptSegmentFromUtterance,
} from "./transcriptSegments";

export interface LocalTranscriptState {
  recordingId: RecordingId;
  revision: number;
  segments: TranscriptSegment[];
  deferredById: Record<string, TranscriptSegment | null>;
}

interface ApplyTranscriptDeltaOptions {
  currentState: LocalTranscriptState | null;
  recordingId: RecordingId;
  fallbackSegments: TranscriptSegment[];
  delta: TranscriptUtteranceList;
  mode?: "full" | "delta";
  activeEditUtteranceId?: string | null;
}

export const createLocalTranscriptState = (
  recordingId: RecordingId,
  segments: TranscriptSegment[],
  revision = 0,
  deferredById: Record<string, TranscriptSegment | null> = {},
): LocalTranscriptState => {
  return {
    recordingId,
    revision,
    segments: sortTranscriptSegments(segments),
    deferredById,
  };
};

export const applyTranscriptDelta = ({
  currentState,
  recordingId,
  fallbackSegments,
  delta,
  mode = "delta",
  activeEditUtteranceId,
}: ApplyTranscriptDeltaOptions): LocalTranscriptState => {
  const baseState =
    currentState && currentState.recordingId === recordingId
      ? currentState
      : createLocalTranscriptState(recordingId, fallbackSegments);
  if (mode === "delta" && delta.revision < baseState.revision) {
    return baseState;
  }
  const nextDeferred = { ...baseState.deferredById };
  let utterancesToApply = delta.utterances;
  let tombstonesToApply = delta.tombstones;

  if (activeEditUtteranceId) {
    const deferredUtterance = utterancesToApply.find(
      (utterance) => utterance.id === activeEditUtteranceId,
    );

    if (deferredUtterance) {
      nextDeferred[activeEditUtteranceId] = transcriptSegmentFromUtterance(
        deferredUtterance,
      );
      utterancesToApply = utterancesToApply.filter(
        (utterance) => utterance.id !== activeEditUtteranceId,
      );
    }

    if (tombstonesToApply.includes(activeEditUtteranceId)) {
      nextDeferred[activeEditUtteranceId] = null;
      tombstonesToApply = tombstonesToApply.filter(
        (utteranceId) => utteranceId !== activeEditUtteranceId,
      );
    }
  }

  const nextSegments =
    mode === "full"
      ? utterancesToApply.length > 0
        ? sortTranscriptSegments(
            utterancesToApply.map(transcriptSegmentFromUtterance),
          )
        : sortTranscriptSegments(fallbackSegments)
      : mergeTranscriptUtteranceDelta(
          baseState.segments,
          utterancesToApply,
          tombstonesToApply,
        );

  return {
    recordingId,
    revision: delta.revision,
    segments: nextSegments,
    deferredById: nextDeferred,
  };
};

export const flushDeferredTranscriptState = (
  currentState: LocalTranscriptState,
): LocalTranscriptState => {
  const legacySegments: TranscriptSegment[] = [];
  const segmentsById = new Map<string, TranscriptSegment>();

  currentState.segments.forEach((segment) => {
    if (!segment.id) {
      legacySegments.push(segment);
      return;
    }

    segmentsById.set(segment.id, segment);
  });

  Object.entries(currentState.deferredById).forEach(([utteranceId, segment]) => {
    if (segment) {
      segmentsById.set(utteranceId, segment);
      return;
    }

    segmentsById.delete(utteranceId);
  });

  return {
    ...currentState,
    segments: sortTranscriptSegments([...legacySegments, ...segmentsById.values()]),
    deferredById: {},
  };
};
