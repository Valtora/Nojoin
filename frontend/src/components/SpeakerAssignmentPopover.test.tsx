import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SpeakerAssignmentPopover from "./SpeakerAssignmentPopover";
import type { RecordingSpeaker } from "@/types";

function createTargetElement() {
  const target = document.createElement("button");
  target.getBoundingClientRect = () =>
    ({
      top: 10,
      left: 20,
      bottom: 40,
      right: 120,
      width: 100,
      height: 30,
      x: 20,
      y: 10,
      toJSON: () => ({}),
    }) as DOMRect;
  document.body.appendChild(target);
  return target;
}

function buildSpeaker(overrides: Partial<RecordingSpeaker>): RecordingSpeaker {
  return {
    id: overrides.id ?? 1,
    created_at: overrides.created_at ?? "2026-05-20T00:00:00Z",
    updated_at: overrides.updated_at ?? "2026-05-20T00:00:00Z",
    recording_id: overrides.recording_id ?? "rec-1",
    diarization_label: overrides.diarization_label ?? "SPEAKER_00",
    local_name: overrides.local_name,
    name: overrides.name,
    global_speaker_id: overrides.global_speaker_id,
    global_speaker: overrides.global_speaker,
    snippet_start: overrides.snippet_start,
    snippet_end: overrides.snippet_end,
    voice_snippet_path: overrides.voice_snippet_path,
    has_voiceprint: overrides.has_voiceprint,
    color: overrides.color,
    merged_into_id: overrides.merged_into_id,
  };
}

describe("SpeakerAssignmentPopover", () => {
  it("includes the selected scope when assigning an existing recording speaker", () => {
    const onSelect = vi.fn();
    const targetElement = createTargetElement();

    render(
      <SpeakerAssignmentPopover
        availableSpeakers={[
          buildSpeaker({ id: 1, diarization_label: "SPEAKER_00", name: "Speaker 1" }),
          buildSpeaker({ id: 2, diarization_label: "SPEAKER_01", local_name: "Dana" }),
        ]}
        globalSpeakers={[]}
        currentSpeakerLabel="SPEAKER_00"
        onSelect={onSelect}
        onClose={() => {}}
        speakerColors={{ SPEAKER_01: "orange" }}
        targetElement={targetElement}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /From here forward/i }));
    fireEvent.click(screen.getByRole("button", { name: /Dana/i }));

    expect(onSelect).toHaveBeenCalledWith({
      name: "Dana",
      diarizationLabel: "SPEAKER_01",
      scope: "from_this_utterance_forward",
    });

    targetElement.remove();
  });

  it("defaults live speaker edits to from-this-utterance-forward for new local names", () => {
    const onSelect = vi.fn();
    const targetElement = createTargetElement();

    render(
      <SpeakerAssignmentPopover
        availableSpeakers={[
          buildSpeaker({ id: 1, diarization_label: "LIVE_01", name: "Speaker 1" }),
        ]}
        globalSpeakers={[]}
        currentSpeakerLabel="LIVE_01"
        onSelect={onSelect}
        onClose={() => {}}
        speakerColors={{}}
        targetElement={targetElement}
      />,
    );

    const fromHereForwardButton = screen.getByRole("button", {
      name: /From here forward/i,
    });
    expect(fromHereForwardButton).toHaveAttribute("aria-pressed", "true");

    fireEvent.change(screen.getByPlaceholderText("Search or add..."), {
      target: { value: "Alex" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create local/i }));

    expect(onSelect).toHaveBeenCalledWith({
      name: "Alex",
      scope: "from_this_utterance_forward",
    });

    targetElement.remove();
  });
});
