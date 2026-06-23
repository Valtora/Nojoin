import { describe, expect, it } from "vitest";

import {
  buildMeetingSpeakerColors,
  buildRecordingSpeakerDisplayMap,
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
