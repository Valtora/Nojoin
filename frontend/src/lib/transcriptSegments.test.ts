import { describe, expect, it } from "vitest";

import {
  diffTranscriptSegments,
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