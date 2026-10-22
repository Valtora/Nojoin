import { describe, expect, it } from "vitest";

import type { TranscriptUtterance } from "@/types";

import {
  applyTranscriptDelta,
  createLocalTranscriptState,
  flushDeferredTranscriptState,
  type LocalTranscriptState,
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

  it("does not apply stale delta revisions", () => {
    const currentState = createLocalTranscriptState(
      "rec-1",
      [
        {
          id: "utt-1",
          start: 1,
          end: 2,
          text: "newer local state",
          speaker: "SPEAKER_00",
          revision: 5,
        },
      ],
      9,
    );

    const nextState = applyTranscriptDelta({
      currentState,
      recordingId: "rec-1",
      fallbackSegments: [],
      delta: {
        recording_id: "rec-1",
        revision: 8,
        utterances: [
          {
            id: "utt-1",
            start: 1,
            end: 2,
            text: "stale remote state",
            speaker: "SPEAKER_01",
            revision: 4,
          },
        ],
        tombstones: [],
        speakers: [],
      },
    });

    expect(nextState).toBe(currentState);
    expect(nextState.revision).toBe(9);
    expect(nextState.segments[0]?.text).toBe("newer local state");
  });

  it("defers tombstones for the utterance currently being edited", () => {
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
        text: "remove now",
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
        revision: 5,
        utterances: [],
        tombstones: ["utt-1", "utt-2"],
        speakers: [],
      },
    });

    expect(nextState.segments.map((segment) => segment.id)).toEqual(["utt-1"]);
    expect(nextState.deferredById["utt-1"]).toBeNull();
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

  describe("a full sync", () => {
    const utterances: TranscriptUtterance[] = [
      {
        id: "utt-1",
        start: 1,
        end: 2,
        text: "one",
        speaker: "SPEAKER_00",
        revision: 1,
        overlapping_speakers: ["SPEAKER_00", "SPEAKER_01"],
      },
      { id: "utt-2", start: 3, end: 4, text: "two", speaker: "SPEAKER_01", revision: 1 },
      { id: "utt-3", start: 5, end: 6, text: "three", speaker: "SPEAKER_00", revision: 4 },
    ];
    const fullSync = (
      currentState: LocalTranscriptState | null,
      next: TranscriptUtterance[],
    ): LocalTranscriptState =>
      applyTranscriptDelta({
        currentState,
        recordingId: "rec-1",
        fallbackSegments: [],
        delta: { recording_id: "rec-1", revision: 9, utterances: next, tombstones: [], speakers: [] },
        mode: "full",
      });
    // As the API hands them over: new objects, nested arrays included.
    const copied = (list: TranscriptUtterance[]) =>
      list.map((utterance) => structuredClone(utterance));

    it("hands back the same list when nothing changed, and a new one when a line goes", () => {
      const first = fullSync(null, utterances);
      const second = fullSync(first, copied(utterances));

      expect(second.segments).toBe(first.segments);

      const third = fullSync(second, copied(utterances.slice(0, 2)));

      expect(third.segments).not.toBe(second.segments);
      expect(third.segments).toEqual([second.segments[0], second.segments[1]]);
      expect(third.segments[0]).toBe(second.segments[0]);
      expect(third.segments[1]).toBe(second.segments[1]);
    });

    it("takes a changed utterance's new segment and keeps the rest", () => {
      const first = fullSync(null, utterances);
      const next = copied(utterances);
      next[1].text = "two, edited";
      next[1].revision = 2;
      const second = fullSync(first, next);

      expect(second.segments).not.toBe(first.segments);
      expect(second.segments[0]).toBe(first.segments[0]);
      expect(second.segments[1]).not.toBe(first.segments[1]);
      expect(second.segments[1].text).toBe("two, edited");
      expect(second.segments[2]).toBe(first.segments[2]);
    });

    it("takes an overlap relabelled at the same length, though the revision is unchanged", () => {
      // A speaker reassign rewrites the overlap labels of the lines around it
      // without bumping their revision.
      const first = fullSync(null, utterances);
      const next = copied(utterances);
      next[0].overlapping_speakers = ["SPEAKER_00", "SPEAKER_02"];
      const second = fullSync(first, next);

      expect(second.segments[0]).not.toBe(first.segments[0]);
      expect(second.segments[0].overlapping_speakers).toEqual(["SPEAKER_00", "SPEAKER_02"]);
      expect(second.segments[1]).toBe(first.segments[1]);
    });

    it("takes a change of revision alone", () => {
      // An edit sends the line's revision back as expected_revision, so a
      // stale one would be refused.
      const first = fullSync(null, utterances);
      const next = copied(utterances);
      next[0].revision = 2;
      const second = fullSync(first, next);

      expect(second.segments[0]).not.toBe(first.segments[0]);
      expect(second.segments[0].revision).toBe(2);
      expect(second.segments[1]).toBe(first.segments[1]);
    });
  });
});
