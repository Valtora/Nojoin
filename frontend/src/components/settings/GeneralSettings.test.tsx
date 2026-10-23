import { describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";
import type { Settings } from "@/types";

import GeneralSettings from "./GeneralSettings";

const TRANSCRIPTION = "Mute non-speech for transcription";
const SPEAKERS = "Mute non-speech for speaker separation";

function renderProcessing(settings: Settings) {
  const onUpdate = vi.fn();
  renderWithProviders(
    <GeneralSettings settings={settings} onUpdate={onUpdate} sections={["processing"]} />,
  );
  return onUpdate;
}

describe("GeneralSettings processing: muting per stage (#486)", () => {
  it("shows transcription muted and speaker separation unmuted until set", () => {
    renderProcessing({});

    expect(screen.getByRole("switch", { name: TRANSCRIPTION })).toBeChecked();
    expect(screen.getByRole("switch", { name: SPEAKERS })).not.toBeChecked();
  });

  it("turns speaker separation's muting back on", () => {
    const onUpdate = renderProcessing({ enable_vad: true });

    fireEvent.click(screen.getByRole("switch", { name: SPEAKERS }));

    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ vad_mute_diarization: true }),
    );
  });

  it("turns off one stage's muting and leaves the other's as it was", () => {
    const onUpdate = renderProcessing({ enable_vad: true, vad_mute_diarization: true });

    fireEvent.click(screen.getByRole("switch", { name: TRANSCRIPTION }));

    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ vad_mute_transcription: false, vad_mute_diarization: true }),
    );
  });

  it("disables both switches while voice activity detection is off", () => {
    const onUpdate = renderProcessing({ enable_vad: false });

    const transcription = screen.getByRole("switch", { name: TRANSCRIPTION });
    expect(transcription).toBeDisabled();
    expect(screen.getByRole("switch", { name: SPEAKERS })).toBeDisabled();
    fireEvent.click(transcription);
    expect(onUpdate).not.toHaveBeenCalled();
  });
});
