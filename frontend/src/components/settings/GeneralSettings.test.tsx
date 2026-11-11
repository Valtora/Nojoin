import { describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";
import type { Settings } from "@/types";

import GeneralSettings from "./GeneralSettings";

const SPEAKERS = "Faster speaker separation";

function renderProcessing(settings: Settings) {
  const onUpdate = vi.fn();
  renderWithProviders(
    <GeneralSettings settings={settings} onUpdate={onUpdate} sections={["processing"]} />,
  );
  return onUpdate;
}

describe("GeneralSettings processing: faster speaker separation (#486, #961)", () => {
  it("shows faster speaker separation off until set", () => {
    renderProcessing({});

    expect(screen.getByRole("switch", { name: SPEAKERS })).not.toBeChecked();
  });

  it("offers no switch for muting before transcription", () => {
    renderProcessing({});

    expect(screen.queryByRole("switch", { name: /transcription/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Mute non-speech/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("switch").map((s) => s.getAttribute("aria-labelledby"))).toEqual([
      "recording-vad-label",
      "recording-vad-mute-diarization-label",
      "recording-remeasure-delivery-label",
      "recording-diarization-label",
    ]);
  });

  it("turns faster speaker separation on", () => {
    const onUpdate = renderProcessing({ enable_vad: true });

    fireEvent.click(screen.getByRole("switch", { name: SPEAKERS }));

    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ vad_mute_diarization: true }),
    );
  });

  it("says what it trades, in outcome terms", () => {
    renderProcessing({});

    expect(screen.getByRole("switch", { name: SPEAKERS })).toHaveAccessibleDescription(
      /Long recordings finish sooner.*speakers are told apart less well/,
    );
  });

  it("disables the switch while voice activity detection is off", () => {
    const onUpdate = renderProcessing({ enable_vad: false });

    const speakers = screen.getByRole("switch", { name: SPEAKERS });
    expect(speakers).toBeDisabled();
    fireEvent.click(speakers);
    expect(onUpdate).not.toHaveBeenCalled();
  });
});
