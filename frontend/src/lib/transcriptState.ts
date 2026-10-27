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

/**
 * Whether two values of segment-shaped plain data (primitives, arrays and
 * plain objects) hold the same data. Key order doesn't matter; a key that is
 * absent differs from one that holds undefined.
 */
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
 * same id when the two hold the same data, and `previous` itself when nothing
 * changed. A full sync builds every segment anew from the server's
 * utterances; the transcript redraws a line whose segment object changes, and
 * the views recompute when the list does. Keeping what is unchanged redraws
 * only the lines that changed, as a delta does.
 *
 * Every field is compared, not only the revision: some changes reach a line
 * without bumping it, such as the overlap labels a speaker reassign rewrites
 * on the lines around it. A segment without an id is never kept; ids are
 * unique, and were one repeated, only the last segment under it is a candidate.
 */
const keepUnchangedSegments = (
  previous: TranscriptSegment[],
  next: TranscriptSegment[],
): TranscriptSegment[] => {
  const previousById = new Map<string, TranscriptSegment>();
  previous.forEach((segment) => {
    if (segment.id) previousById.set(segment.id, segment);
  });
  const result = next.map((segment) => {
    const kept = segment.id ? previousById.get(segment.id) : undefined;
    return kept && sameJsonValue(kept, segment) ? kept : segment;
  });
  return result.length === previous.length &&
    result.every((segment, at) => segment === previous[at])
    ? previous
    : result;
};

export const applyTranscriptDelta = ({
  currentState,
  recordingId,
  fallbackSegments,
  delta,
  mode = "delta",
  activeEditUtteranceId,
}: ApplyTranscriptDeltaOptions): LocalTranscriptState => {
  const previousState =
    currentState && currentState.recordingId === recordingId
      ? currentState
      : createLocalTranscriptState(recordingId, fallbackSegments);
  const baseState = delta.settling_required && previousState.segments.some((segment) => !segment.settling_required)
    ? { ...previousState, segments: previousState.segments.map((segment) => ({ ...segment, settling_required: true })) }
    : previousState;
  if (mode === "delta" && delta.revision < baseState.revision) {
    return baseState;
  }
  const nextDeferred = { ...baseState.deferredById };
  let utterancesToApply = delta.settling_required
    ? delta.utterances.map((utterance) => ({ ...utterance, settling_required: true }))
    : delta.utterances;
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

  // A delta that applies nothing keeps the list, as a full sync does when
  // nothing changed, so a poll with no news doesn't recompute the views.
  const nextSegments =
    mode === "full"
      ? utterancesToApply.length > 0
        ? keepUnchangedSegments(
            baseState.segments,
            sortTranscriptSegments(utterancesToApply.map(transcriptSegmentFromUtterance)),
          )
        : delta.settling_required ? [] : sortTranscriptSegments(fallbackSegments)
      : utterancesToApply.length === 0 && tombstonesToApply.length === 0
        ? baseState.segments
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
