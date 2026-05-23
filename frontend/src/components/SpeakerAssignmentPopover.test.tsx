import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SpeakerAssignmentPopover from "./SpeakerAssignmentPopover";
import type { RecordingSpeaker } from "@/types";

function createTargetElement(rectOverrides: Partial<DOMRect> = {}) {
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
      ...rectOverrides,
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
  it("uses utterance scope by default and can switch to whole transcript", () => {
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

    expect(screen.getByRole("button", { name: /This utterance/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(screen.getByText("Dana").closest("button") as HTMLButtonElement);

    expect(onSelect).toHaveBeenCalledWith({
      name: "Dana",
      diarizationLabel: "SPEAKER_01",
      scope: "utterance_only",
    });

    fireEvent.click(screen.getByRole("button", { name: /Whole transcript/i }));
    fireEvent.click(screen.getByText("Dana").closest("button") as HTMLButtonElement);

    expect(onSelect).toHaveBeenLastCalledWith({
      name: "Dana",
      diarizationLabel: "SPEAKER_01",
      scope: "speaker_everywhere_in_recording",
    });

    targetElement.remove();
  });

  it("does not expose from-here-forward scope for live speaker edits", () => {
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

    expect(
      screen.queryByRole("button", { name: /From here forward/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Whole transcript/i }));

    fireEvent.change(screen.getByPlaceholderText("Search or add..."), {
      target: { value: "Alex" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Create local/i }));

    expect(onSelect).toHaveBeenCalledWith({
      name: "Alex",
      scope: "speaker_everywhere_in_recording",
    });

    targetElement.remove();
  });

  it("flips above the target when opened near the bottom of the viewport", () => {
    const onSelect = vi.fn();
    const targetElement = createTargetElement({
      top: 720,
      bottom: 750,
      y: 720,
    });
    const originalInnerHeight = window.innerHeight;
    const originalInnerWidth = window.innerWidth;

    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 768,
    });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1024,
    });

    render(
      <SpeakerAssignmentPopover
        availableSpeakers={[]}
        globalSpeakers={[
          {
            id: 1,
            name: "Simon Whistler",
            created_at: "2026-05-20T00:00:00Z",
            updated_at: "2026-05-20T00:00:00Z",
          },
        ]}
        currentSpeakerLabel="SPEAKER_00"
        onSelect={onSelect}
        onClose={() => {}}
        speakerColors={{}}
        targetElement={targetElement}
      />,
    );

    const popover = screen.getByPlaceholderText("Search or add...")
      .closest("div.fixed") as HTMLDivElement;

    expect(Number.parseFloat(popover.style.top)).toBeLessThan(720);
    expect(popover.style.width).toBe("360px");
    expect(Number.parseFloat(popover.style.maxHeight)).toBeLessThanOrEqual(752);

    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: originalInnerHeight,
    });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: originalInnerWidth,
    });
    targetElement.remove();
  });
});
