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

/** Whether two values parsed from the API's JSON hold the same data. */
const sameJsonValue = (left: unknown, right: unknown): boolean => {
  if (Object.is(left, right)) return true;
  if (
    typeof left !== "object" ||
    typeof right !== "object" ||
    left === null ||
    right === null ||
    Array.isArray(left) !== Array.isArray(right)
  ) {
    return false;
  }
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const keys = Object.keys(leftRecord);
  return (
    keys.length === Object.keys(rightRecord).length &&
    keys.every(
      (key) => Object.hasOwn(rightRecord, key) && sameJsonValue(leftRecord[key], rightRecord[key]),
    )
  );
};

/**
 * `next`, with each segment swapped for the one `previous` holds under the
 * same id when the two hold the same data. A full sync builds every segment
 * anew from the server's utterances, and the transcript redraws a line whose
 * segment object changes; keeping the unchanged ones redraws only the lines
 * that changed, as a delta does. Every field is compared, so an unchanged
 * revision can't hide a change. A segment without an id is never kept.
 */
const keepUnchangedSegments = (
  previous: TranscriptSegment[],
  next: TranscriptSegment[],
): TranscriptSegment[] => {
  const previousById = new Map<string, TranscriptSegment>();
  previous.forEach((segment) => {
    if (segment.id) previousById.set(segment.id, segment);
  });
  return next.map((segment) => {
    const kept = segment.id ? previousById.get(segment.id) : undefined;
    return kept && sameJsonValue(kept, segment) ? kept : segment;
  });
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
        ? keepUnchangedSegments(
            baseState.segments,
            sortTranscriptSegments(utterancesToApply.map(transcriptSegmentFromUtterance)),
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
