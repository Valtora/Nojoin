import {
  TranscriptSegment,
  TranscriptSpeakerAssignment,
  TranscriptUtterance,
} from "@/types";

export interface TranscriptSegmentChange {
  before: TranscriptSegment;
  after: TranscriptSegment;
  changedFields: Array<"text" | "speaker">;
}

export interface RollingSpeakerCorrectionHistory {
  beforeSpeaker: string;
  afterSpeaker: string;
  beforeRecordingSpeakerId?: number;
  afterRecordingSpeakerId?: number;
  targetPreexisted: boolean;
}

export interface TranscriptSpeakerHistoryItemLike {
  patches: TranscriptSegmentChange[];
  rollingSpeakerCorrection?: RollingSpeakerCorrectionHistory;
}

export const buildSpeakerHistoryAssignment = (
  segment: Pick<TranscriptSegment, "speaker">,
): TranscriptSpeakerAssignment => ({
  name: segment.speaker,
  diarizationLabel: segment.speaker,
  scope: "utterance_only",
});

export const buildRollingSpeakerCorrectionHistory = ({
  previousSegments,
  sourceSegment,
  updatedSegment,
}: {
  previousSegments: TranscriptSegment[];
  sourceSegment: TranscriptSegment;
  updatedSegment: TranscriptSegment | undefined;
}): RollingSpeakerCorrectionHistory | undefined => {
  if (!updatedSegment || sourceSegment.speaker === updatedSegment.speaker) {
    return undefined;
  }

  const targetPreexisted = previousSegments.some(
    (segment) =>
      segment.id !== sourceSegment.id &&
      segment.speaker === updatedSegment.speaker &&
      (updatedSegment.recording_speaker_id === undefined ||
        segment.recording_speaker_id === updatedSegment.recording_speaker_id),
  );

  return {
    beforeSpeaker: sourceSegment.speaker,
    afterSpeaker: updatedSegment.speaker,
    beforeRecordingSpeakerId: sourceSegment.recording_speaker_id,
    afterRecordingSpeakerId: updatedSegment.recording_speaker_id,
    targetPreexisted,
  };
};

export const extendRollingSpeakerHistoryWithSegments = <
  THistoryItem extends TranscriptSpeakerHistoryItemLike,
>(
  historyItems: THistoryItem[],
  previousSegments: TranscriptSegment[],
  nextSegments: TranscriptSegment[],
): THistoryItem[] => {
  if (historyItems.length === 0 || nextSegments.length === 0) {
    return historyItems;
  }

  const previousById = new Map(
    previousSegments
      .filter((segment): segment is TranscriptSegment & { id: string } =>
        Boolean(segment.id),
      )
      .map((segment) => [segment.id, segment]),
  );
  let changed = false;

  const nextHistoryItems = historyItems.map((item) => {
    const rolling = item.rollingSpeakerCorrection;
    if (!rolling || rolling.targetPreexisted) {
      return item;
    }

    const patchedIds = new Set(
      item.patches
        .map((patch) => patch.after.id || patch.before.id)
        .filter((id): id is string => Boolean(id)),
    );
    const additions: TranscriptSegmentChange[] = [];

    nextSegments.forEach((segment) => {
      if (!segment.id || patchedIds.has(segment.id)) {
        return;
      }
      if (segment.speaker !== rolling.afterSpeaker) {
        return;
      }
      if (
        rolling.afterRecordingSpeakerId !== undefined &&
        segment.recording_speaker_id !== rolling.afterRecordingSpeakerId
      ) {
        return;
      }

      const previousSegment = previousById.get(segment.id);
      if (previousSegment && previousSegment.speaker === segment.speaker) {
        return;
      }

      additions.push({
        before: {
          ...segment,
          speaker: rolling.beforeSpeaker,
          recording_speaker_id: rolling.beforeRecordingSpeakerId,
        },
        after: segment,
        changedFields: ["speaker"],
      });
      patchedIds.add(segment.id);
    });

    if (additions.length === 0) {
      return item;
    }

    changed = true;
    return {
      ...item,
      patches: [...item.patches, ...additions],
    };
  });

  return changed ? nextHistoryItems : historyItems;
};

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
    speaker_assignment_source: utterance.speaker_assignment_source,
    speaker_assignment_authority: utterance.speaker_assignment_authority,
    updated_at: utterance.updated_at,
    speaker_state_source: utterance.speaker_state_source,
    live_source_speaker: utterance.live_source_speaker,
    live_source_speakers: utterance.live_source_speakers,
    source_public_ids: utterance.source_public_ids,
    live_reuse_alignment: utterance.live_reuse_alignment,
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
