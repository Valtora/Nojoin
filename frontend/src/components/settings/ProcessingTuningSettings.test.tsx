import { describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen } from "@testing-library/react";

import { PROCESSING_TUNING_KEYS } from "@/lib/processingTuning";
import type { Settings } from "@/types";

import ProcessingTuningSettings from "./ProcessingTuningSettings";

function renderTuning(settings: Settings) {
  const onUpdate = vi.fn();
  const view = render(
    <ProcessingTuningSettings settings={settings} onUpdate={onUpdate} />,
  );
  return { onUpdate, view };
}

describe("ProcessingTuningSettings", () => {
  it("resets every tuning value to inherit, keeping other settings", () => {
    const { onUpdate } = renderTuning({
      theme: "dark",
      vad_threshold: 0.3,
      speaker_merge_threshold: 0.6,
    });

    fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));

    const next = onUpdate.mock.calls[0][0] as Settings;
    expect(next.theme).toBe("dark");
    for (const key of PROCESSING_TUNING_KEYS) {
      expect(next[key], key).toBeNull();
    }
  });

  it("sends a typed value as a number and an emptied field as null", () => {
    const { onUpdate } = renderTuning({ vad_threshold: 0.3 });
    const input = screen.getByRole("spinbutton", { name: "Speech detection threshold" });

    fireEvent.change(input, { target: { value: "0.25" } });
    fireEvent.change(input, { target: { value: "" } });

    expect(onUpdate.mock.calls[0][0].vad_threshold).toBe(0.25);
    expect(onUpdate.mock.calls[1][0].vad_threshold).toBeNull();
  });

  it("shows a reset value as empty with the default as placeholder", () => {
    const { view, onUpdate } = renderTuning({ vad_threshold: 0.3 });
    const input = screen.getByRole("spinbutton", { name: "Speech detection threshold" });
    expect(input).toHaveValue(0.3);

    view.rerender(
      <ProcessingTuningSettings settings={{ vad_threshold: null }} onUpdate={onUpdate} />,
    );

    expect(input).toHaveValue(null);
    expect(input).toHaveAttribute("placeholder", "Default (0.5)");
  });

  it("labels each field of a multi-field row", () => {
    renderTuning({});

    expect(screen.getByRole("spinbutton", { name: "Merge similarity" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Max gap (seconds)" })).toBeInTheDocument();
  });
});
