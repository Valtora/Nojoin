import { describe, expect, it } from "vitest";

import {
  buildRollingSpeakerCorrectionHistory,
  buildSpeakerHistoryAssignment,
  diffTranscriptSegments,
  extendRollingSpeakerHistoryWithSegments,
  getTranscriptSegmentKey,
  mergeTranscriptUtteranceDelta,
  transcriptSegmentFromUtterance,
} from "./transcriptSegments";

describe("transcriptSegments", () => {
  it("maps canonical utterances into transcript segment display fields", () => {
    expect(
      transcriptSegmentFromUtterance({
        id: "utt-1",
        start: 1,
        end: 3,
        text: "Hello",
        speaker: "SPEAKER_00",
        revision: 4,
        state: "stable",
        speaker_state: "manual_override",
        provisional: false,
        overlapping_speakers: ["SPEAKER_01"],
        speaker_confidence: 0.91,
        text_confidence: 0.84,
        updated_at: "2026-05-21T10:00:00Z",
        speaker_state_source: "source_channel",
        live_source_speaker: "LIVE_01",
        live_source_speakers: ["LIVE_01", "LIVE_02"],
        source_public_ids: ["utt-live-1"],
        live_reuse_alignment: {
          status: "matched",
          matched_live_utterance_ids: ["utt-live-1"],
        },
      }),
    ).toEqual({
      id: "utt-1",
      start: 1,
      end: 3,
      text: "Hello",
      speaker: "SPEAKER_00",
      recording_speaker_id: undefined,
      state: "stable",
      revision: 4,
      speaker_state: "manual_override",
      overlapping_speakers: ["SPEAKER_01"],
      provisional: false,
      segment_source: undefined,
      speaker_manually_edited: undefined,
      text_manually_edited: undefined,
      speaker_confidence: 0.91,
      text_confidence: 0.84,
      updated_at: "2026-05-21T10:00:00Z",
      speaker_state_source: "source_channel",
      live_source_speaker: "LIVE_01",
      live_source_speakers: ["LIVE_01", "LIVE_02"],
      source_public_ids: ["utt-live-1"],
      live_reuse_alignment: {
        status: "matched",
        matched_live_utterance_ids: ["utt-live-1"],
      },
    });
  });

  it("merges delta updates and tombstones by stable utterance id", () => {
    const merged = mergeTranscriptUtteranceDelta(
      [
        {
          id: "utt-1",
          start: 0,
          end: 2,
          text: "first",
          speaker: "SPEAKER_00",
          revision: 1,
        },
        {
          id: "utt-2",
          start: 3,
          end: 4,
          text: "remove me",
          speaker: "SPEAKER_00",
          revision: 1,
        },
      ],
      [
        {
          id: "utt-1",
          start: 0,
          end: 2,
          text: "first updated",
          speaker: "Dana",
          revision: 2,
        },
        {
          id: "utt-3",
          start: 5,
          end: 7,
          text: "new",
          speaker: "SPEAKER_01",
          revision: 1,
        },
      ],
      ["utt-2"],
    );

    expect(merged).toEqual([
      {
        id: "utt-1",
        start: 0,
        end: 2,
        text: "first updated",
        speaker: "Dana",
        recording_speaker_id: undefined,
        state: undefined,
        revision: 2,
        speaker_state: undefined,
        overlapping_speakers: undefined,
        provisional: undefined,
        segment_source: undefined,
        speaker_manually_edited: undefined,
        text_manually_edited: undefined,
        speaker_confidence: undefined,
        text_confidence: undefined,
        updated_at: undefined,
        speaker_state_source: undefined,
        live_source_speaker: undefined,
        live_source_speakers: undefined,
        source_public_ids: undefined,
        live_reuse_alignment: undefined,
      },
      {
        id: "utt-3",
        start: 5,
        end: 7,
        text: "new",
        speaker: "SPEAKER_01",
        recording_speaker_id: undefined,
        state: undefined,
        revision: 1,
        speaker_state: undefined,
        overlapping_speakers: undefined,
        provisional: undefined,
        segment_source: undefined,
        speaker_manually_edited: undefined,
        text_manually_edited: undefined,
        speaker_confidence: undefined,
        text_confidence: undefined,
        updated_at: undefined,
        speaker_state_source: undefined,
        live_source_speaker: undefined,
        live_source_speakers: undefined,
        source_public_ids: undefined,
        live_reuse_alignment: undefined,
      },
    ]);
  });

  it("diffs text and speaker changes by stable utterance id", () => {
    const changes = diffTranscriptSegments(
      [
        {
          id: "utt-1",
          start: 0,
          end: 1,
          text: "old text",
          speaker: "SPEAKER_00",
          revision: 1,
        },
      ],
      [
        {
          id: "utt-1",
          start: 0,
          end: 1,
          text: "new text",
          speaker: "Dana",
          revision: 2,
        },
      ],
    );

    expect(changes).toHaveLength(1);
    expect(changes[0]?.changedFields).toEqual(["text", "speaker"]);
  });

  it("builds utterance-scoped speaker assignments from historical labels", () => {
    expect(
      buildSpeakerHistoryAssignment({
        speaker: "MANUAL_1234abcd",
      }),
    ).toEqual({
      name: "MANUAL_1234abcd",
      diarizationLabel: "MANUAL_1234abcd",
      scope: "utterance_only",
    });
  });

  it("extends a rolling speaker correction with later live segments for a newly introduced target", () => {
    const firstBefore = {
      id: "utt-1",
      start: 0,
      end: 1,
      text: "hello",
      speaker: "LIVE_01",
      recording_speaker_id: 1,
      revision: 1,
    };
    const firstAfter = {
      ...firstBefore,
      speaker: "MANUAL_1234abcd",
      recording_speaker_id: 9,
      revision: 2,
    };
    const rollingSpeakerCorrection = buildRollingSpeakerCorrectionHistory({
      previousSegments: [firstBefore],
      sourceSegment: firstBefore,
      updatedSegment: firstAfter,
    });
    const history = [
      {
        description: "Change speaker utt-1",
        rollingSpeakerCorrection,
        patches: [
          {
            before: firstBefore,
            after: firstAfter,
            changedFields: ["speaker" as const],
          },
        ],
      },
    ];
    const laterSegment = {
      id: "utt-2",
      start: 2,
      end: 3,
      text: "later",
      speaker: "MANUAL_1234abcd",
      recording_speaker_id: 9,
      revision: 1,
      provisional: true,
      segment_source: "live",
    };

    const extended = extendRollingSpeakerHistoryWithSegments(
      history,
      [firstAfter],
      [firstAfter, laterSegment],
    );

    expect(extended[0]?.patches).toHaveLength(2);
    expect(extended[0]?.patches[1]).toMatchObject({
      before: {
        id: "utt-2",
        speaker: "LIVE_01",
        recording_speaker_id: 1,
      },
      after: {
        id: "utt-2",
        speaker: "MANUAL_1234abcd",
        recording_speaker_id: 9,
      },
      changedFields: ["speaker"],
    });
  });

  it("does not roll speaker history when the target speaker already existed", () => {
    const sourceBefore = {
      id: "utt-1",
      start: 0,
      end: 1,
      text: "hello",
      speaker: "LIVE_01",
      recording_speaker_id: 1,
      revision: 1,
    };
    const existingTarget = {
      id: "utt-existing",
      start: 1,
      end: 2,
      text: "target",
      speaker: "LIVE_02",
      recording_speaker_id: 2,
      revision: 1,
    };
    const sourceAfter = {
      ...sourceBefore,
      speaker: "LIVE_02",
      recording_speaker_id: 2,
      revision: 2,
    };
    const rollingSpeakerCorrection = buildRollingSpeakerCorrectionHistory({
      previousSegments: [sourceBefore, existingTarget],
      sourceSegment: sourceBefore,
      updatedSegment: sourceAfter,
    });
    const history = [
      {
        description: "Change speaker utt-1",
        rollingSpeakerCorrection,
        patches: [
          {
            before: sourceBefore,
            after: sourceAfter,
            changedFields: ["speaker" as const],
          },
        ],
      },
    ];

    const extended = extendRollingSpeakerHistoryWithSegments(
      history,
      [sourceAfter, existingTarget],
      [
        sourceAfter,
        existingTarget,
        {
          id: "utt-2",
          start: 2,
          end: 3,
          text: "later",
          speaker: "LIVE_02",
          recording_speaker_id: 2,
          revision: 1,
        },
      ],
    );

    expect(rollingSpeakerCorrection?.targetPreexisted).toBe(true);
    expect(extended[0]?.patches).toHaveLength(1);
  });

  it("builds deterministic legacy keys when no stable id is present", () => {
    expect(
      getTranscriptSegmentKey(
        {
          start: 1,
          end: 2,
          speaker: "UNKNOWN",
        },
        4,
      ),
    ).toBe("legacy-4-1-2-UNKNOWN");
  });
});
