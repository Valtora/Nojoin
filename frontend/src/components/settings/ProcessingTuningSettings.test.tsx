import { useState } from "react";
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

/**
 * Hosts the card on real state, as SettingsProvider does, so a value the card
 * writes is fed back to it on the next render. ``latest`` reads that state.
 */
function renderWithState(initial: Settings) {
  let latest = initial;
  function Host() {
    const [settings, setSettings] = useState(initial);
    const update = (next: Settings) => {
      latest = next;
      setSettings(next);
    };
    return <ProcessingTuningSettings settings={settings} onUpdate={update} />;
  }
  render(<Host />);
  return { latest: () => latest };
}

/**
 * Types text the browser cannot parse into a number input. jsdom never sets
 * validity.badInput, so it is stubbed the way a browser reports it: the value
 * reads as "" while badInput is true.
 */
function typeUnparseable(input: HTMLElement) {
  Object.defineProperty(input, "validity", {
    value: { badInput: true },
    configurable: true,
  });
  fireEvent.change(input, { target: { value: "" } });
}

describe("ProcessingTuningSettings", () => {
  it("resets every tuning value to inherit, keeping other settings", () => {
    const { onUpdate } = renderTuning({
      theme: "dark",
      vad_threshold: 0.3,
      phantom_merge_threshold: 0.7,
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

  it("keeps text the browser cannot parse out of the settings", () => {
    const state = renderWithState({ vad_threshold: 0.3 });
    const input = screen.getByRole("spinbutton", { name: "Speech detection threshold" });

    typeUnparseable(input);

    expect(state.latest().vad_threshold).toBe(0.3);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Speech detection threshold must be a number.");
  });

  it("keeps an out-of-range number out of the settings until it is fixed", () => {
    const state = renderWithState({ vad_threshold: 0.3 });
    const input = screen.getByRole("spinbutton", { name: "Speech detection threshold" });

    fireEvent.change(input, { target: { value: "0.95" } });

    expect(state.latest().vad_threshold).toBe(0.3);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/between 0.15 and 0.9/);

    fireEvent.change(input, { target: { value: "0.85" } });

    expect(state.latest().vad_threshold).toBe(0.85);
    expect(input).not.toHaveAttribute("aria-invalid");
  });
});
