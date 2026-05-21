import { describe, expect, it } from "vitest";

import {
  applyTranscriptDelta,
  createLocalTranscriptState,
  flushDeferredTranscriptState,
} from "./transcriptState";

describe("transcriptState", () => {
  it("applies canonical utterance deltas to the local transcript state", () => {
    const nextState = applyTranscriptDelta({
      currentState: createLocalTranscriptState("rec-1", [
        {
          id: "utt-1",
          start: 1,
          end: 2,
          text: "old",
          speaker: "SPEAKER_00",
          revision: 1,
        },
      ]),
      recordingId: "rec-1",
      fallbackSegments: [],
      delta: {
        recording_id: "rec-1",
        revision: 3,
        utterances: [
          {
            id: "utt-1",
            start: 1,
            end: 2,
            text: "updated",
            speaker: "SPEAKER_00",
            revision: 2,
          },
          {
            id: "utt-2",
            start: 3,
            end: 4,
            text: "new",
            speaker: "SPEAKER_01",
            revision: 1,
          },
        ],
        tombstones: [],
        speakers: [],
      },
    });

    expect(nextState.revision).toBe(3);
    expect(nextState.segments.map((segment) => segment.id)).toEqual([
      "utt-1",
      "utt-2",
    ]);
    expect(nextState.segments[0]?.text).toBe("updated");
  });

  it("defers remote updates for the utterance currently being edited", () => {
    const currentState = createLocalTranscriptState("rec-1", [
      {
        id: "utt-1",
        start: 1,
        end: 2,
        text: "local edit",
        speaker: "SPEAKER_00",
        revision: 2,
      },
      {
        id: "utt-2",
        start: 3,
        end: 4,
        text: "stay live",
        speaker: "SPEAKER_01",
        revision: 1,
      },
    ]);

    const nextState = applyTranscriptDelta({
      currentState,
      recordingId: "rec-1",
      fallbackSegments: currentState.segments,
      activeEditUtteranceId: "utt-1",
      delta: {
        recording_id: "rec-1",
        revision: 4,
        utterances: [
          {
            id: "utt-1",
            start: 1,
            end: 2,
            text: "remote update",
            speaker: "Dana",
            revision: 3,
          },
          {
            id: "utt-2",
            start: 3,
            end: 4,
            text: "updated live",
            speaker: "SPEAKER_01",
            revision: 2,
          },
        ],
        tombstones: [],
        speakers: [],
      },
    });

    expect(nextState.segments.find((segment) => segment.id === "utt-1")?.text).toBe(
      "local edit",
    );
    expect(nextState.segments.find((segment) => segment.id === "utt-2")?.text).toBe(
      "updated live",
    );
    expect(nextState.deferredById["utt-1"]?.text).toBe("remote update");
  });

  it("flushes deferred remote updates once editing ends", () => {
    const flushed = flushDeferredTranscriptState({
      recordingId: "rec-1",
      revision: 4,
      segments: [
        {
          id: "utt-1",
          start: 1,
          end: 2,
          text: "local edit",
          speaker: "SPEAKER_00",
          revision: 2,
        },
      ],
      deferredById: {
        "utt-1": {
          id: "utt-1",
          start: 1,
          end: 2,
          text: "remote update",
          speaker: "Dana",
          revision: 3,
        },
        "utt-2": null,
      },
    });

    expect(flushed.deferredById).toEqual({});
    expect(flushed.segments).toEqual([
      {
        id: "utt-1",
        start: 1,
        end: 2,
        text: "remote update",
        speaker: "Dana",
        revision: 3,
      },
    ]);
  });
});