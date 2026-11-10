// @vitest-environment node

import { describe, expect, it } from "vitest";

import {
  buildMeetingSpeakerColors,
  buildRecordingSpeakerDisplayMap,
  isPlaceholderSpeakerName,
  liveSpeakerDisplayName,
  segmentSpeakerDisplayName,
} from "./recordingSpeakerUtils";
import type { RecordingSpeaker } from "@/types";

function buildRecordingSpeaker(
  overrides: Partial<RecordingSpeaker> &
    Pick<RecordingSpeaker, "id" | "recording_id" | "diarization_label">,
): RecordingSpeaker {
  return {
    id: overrides.id,
    created_at: overrides.created_at ?? "2026-05-25T12:00:00Z",
    updated_at: overrides.updated_at ?? "2026-05-25T12:00:00Z",
    recording_id: overrides.recording_id,
    global_speaker_id: overrides.global_speaker_id,
    diarization_label: overrides.diarization_label,
    local_name: overrides.local_name,
    name: overrides.name,
    snippet_start: overrides.snippet_start,
    snippet_end: overrides.snippet_end,
    voice_snippet_path: overrides.voice_snippet_path,
    has_voiceprint: overrides.has_voiceprint,
    global_speaker: overrides.global_speaker,
    color: overrides.color,
    merged_into_id: overrides.merged_into_id,
  };
}

describe("recordingSpeakerUtils", () => {
  it("reserves no speaker colour for pending-only UNKNOWN while retaining covered UNKNOWN", () => {
    const known = [{ speaker: "LIVE_PROVISIONAL_1" }, { speaker: "LIVE_PROVISIONAL_2" }];
    const initial = buildMeetingSpeakerColors({ segments: known });
    const pending = buildMeetingSpeakerColors({ segments: [
      { speaker: "UNKNOWN", diarisation_pending: true }, ...known,
    ] });
    expect(pending).toEqual(initial);
    expect(pending.UNKNOWN).toBeUndefined();
    const covered = buildMeetingSpeakerColors({ segments: [
      ...known, { speaker: "UNKNOWN", diarisation_pending: false },
      { speaker: "UNKNOWN", diarisation_pending: true },
    ], existingColors: pending });
    expect(covered.UNKNOWN).toBeDefined();
    expect(covered.LIVE_PROVISIONAL_1).toBe(initial.LIVE_PROVISIONAL_1);
  });

  it("keeps recording-page colors meeting-local even when a linked global speaker has a color", () => {
    const colors = buildMeetingSpeakerColors({
      segments: [{ speaker: "Dana" }],
      speakers: [
        buildRecordingSpeaker({
          id: 1,
          recording_id: "rec-1",
          diarization_label: "LIVE_00",
          local_name: "Dana",
          color: "amber",
          global_speaker: {
            id: 10,
            created_at: "2026-05-25T12:00:00Z",
            updated_at: "2026-05-25T12:00:00Z",
            name: "Dana Global",
            color: "violet",
          },
        }),
      ],
    });

    expect(colors.Dana).toBe("amber");
    expect(colors.LIVE_00).toBe("amber");
    expect(colors.Dana).not.toBe("violet");
  });

  it("spreads new automatic colors away from meeting colors already in use", () => {
    const colors = buildMeetingSpeakerColors({
      segments: [
        { speaker: "LIVE_00" },
        { speaker: "LIVE_01" },
      ],
      existingColors: {
        LIVE_00: "blue",
      },
    });

    expect(colors.LIVE_00).toBe("blue");
    expect(colors.LIVE_01).toBe("orange");
  });

  it("keeps prior meeting-local assignments stable when new speakers appear", () => {
    const initialColors = buildMeetingSpeakerColors({
      segments: [
        { speaker: "LIVE_00" },
        { speaker: "LIVE_01" },
      ],
      existingColors: {
        LIVE_00: "blue",
      },
    });

    const nextColors = buildMeetingSpeakerColors({
      segments: [
        { speaker: "LIVE_00" },
        { speaker: "LIVE_01" },
        { speaker: "LIVE_02" },
      ],
      existingColors: initialColors,
    });

    expect(nextColors.LIVE_00).toBe(initialColors.LIVE_00);
    expect(nextColors.LIVE_01).toBe(initialColors.LIVE_01);
    expect(nextColors.LIVE_02).toBeDefined();
    expect(nextColors.LIVE_02).not.toBe(initialColors.LIVE_00);
    expect(nextColors.LIVE_02).not.toBe(initialColors.LIVE_01);
  });

  it("maps generic live labels to the renamed speaker display name", () => {
    const speakerMap = buildRecordingSpeakerDisplayMap(
      [
        buildRecordingSpeaker({
          id: 1,
          recording_id: "rec-1",
          diarization_label: "LIVE_01",
          local_name: "Ezra Klein",
        }),
      ],
      new Map(),
    );

    expect(speakerMap.LIVE_01).toBe("Ezra Klein");
    expect(speakerMap["Speaker 1"]).toBe("Ezra Klein");
  });

  it("maps generic diarization labels to the renamed speaker display name", () => {
    const speakerMap = buildRecordingSpeakerDisplayMap(
      [
        buildRecordingSpeaker({
          id: 1,
          recording_id: "rec-1",
          diarization_label: "SPEAKER_00",
          local_name: "Ezra Klein",
        }),
      ],
      new Map(),
    );

    expect(speakerMap.SPEAKER_00).toBe("Ezra Klein");
    expect(speakerMap["Speaker 1"]).toBe("Ezra Klein");
  });
});

describe("live speaker labels", () => {
  it("names only a live label", () => {
    expect(liveSpeakerDisplayName("LIVE_PROVISIONAL_3")).toBe("Speaker 3");
    expect(liveSpeakerDisplayName("LIVE_PROVISIONAL_12")).toBe("Speaker 12");
    // LIVE_3 is the older live label, which the pipeline makes a speaker of.
    for (const label of [
      "SPEAKER_03",
      "UNKNOWN",
      "LIVE_3",
      "LIVE_PROVISIONAL_",
      "LIVE_PROVISIONAL_3a",
      "xLIVE_PROVISIONAL_3",
      null,
      undefined,
    ]) {
      expect(liveSpeakerDisplayName(label)).toBeNull();
    }
  });

  it("prefers the recording's name, then Speaker N, then the label", () => {
    const speakerMap = { SPEAKER_00: "Dana", LIVE_PROVISIONAL_4: "Ada" };
    expect(segmentSpeakerDisplayName(speakerMap, "SPEAKER_00")).toBe("Dana");
    expect(segmentSpeakerDisplayName(speakerMap, "LIVE_PROVISIONAL_4")).toBe("Ada");
    expect(segmentSpeakerDisplayName(speakerMap, "LIVE_PROVISIONAL_7")).toBe("Speaker 7");
    expect(segmentSpeakerDisplayName(speakerMap, "SPEAKER_09")).toBe("SPEAKER_09");
  });
});

// The same cases as backend/tests/test_placeholder_speaker_name.py: the two
// rules must agree.
describe("isPlaceholderSpeakerName", () => {
  it.each([
    "",
    "   ",
    "SPEAKER_03",
    "Speaker 1",
    "unknown",
    "New Voice 3",
    "N:L0",
    "n:l7",
    " N:L12 ",
    "C:SPEAKER_02",
  ])("treats the fallback label %j as a placeholder", (name) => {
    expect(isPlaceholderSpeakerName(name)).toBe(true);
  });

  it.each([
    "Alice Example",
    "N:Dubz",
    "C:Drive",
    "c:3po",
    "C: Drive Team",
    "N:L0 team",
    "C:SPEAKER",
    "Sam: Finance",
  ])("keeps the real name %j", (name) => {
    expect(isPlaceholderSpeakerName(name)).toBe(false);
  });
});
