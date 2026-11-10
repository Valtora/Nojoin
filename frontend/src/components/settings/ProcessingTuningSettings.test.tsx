import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen, within } from "@testing-library/react";

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
  it("resets displayed tuning values to inherit, keeping settings from other pages", () => {
    const { onUpdate } = renderTuning({
      theme: "dark",
      vad_threshold: 0.3,
      phantom_merge_threshold: 0.7,
      line_break_pause_s: 2,
      phantom_filter_off: true,
    });

    fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));

    const next = onUpdate.mock.calls[0][0] as Settings;
    expect(next.phantom_filter_off).toBeNull();
    expect(next.theme).toBe("dark");
    expect(next.line_break_pause_s).toBe(2);
    for (const key of PROCESSING_TUNING_KEYS) {
      // This field has its own control on Transcription, outside this card.
      if (key === "line_break_pause_s") continue;
      expect(next[key], key).toBeNull();
    }
  });

  it("does not introduce a transcription-page pause when resetting unset tuning", () => {
    const { onUpdate } = renderTuning({});

    fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));

    expect(onUpdate.mock.calls[0][0]).not.toHaveProperty("line_break_pause_s");
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

  it("labels each field of a multi-field row within a group named by the row", () => {
    renderTuning({});

    const brief = screen.getByRole("group", { name: "Speakers heard only briefly" });
    const stray = screen.getByRole("group", { name: "Stray one-word speaker changes" });
    expect(within(brief).getAllByRole("spinbutton")).toHaveLength(4);
    for (const name of [
      "Check speakers under (seconds)",
      "And up to (turns)",
      "Treat as noise below (voice match)",
      "Merge from (voice match)",
    ]) {
      expect(within(brief).getByRole("spinbutton", { name })).toBeInTheDocument();
    }
    expect(within(stray).getByRole("spinbutton", { name: "Words up to (seconds)" })).toBeInTheDocument();
    expect(within(stray).getByRole("spinbutton", { name: "Pause up to (seconds)" })).toBeInTheDocument();
  });

  it("shows the longest word only when Parakeet is the engine", () => {
    const { view, onUpdate } = renderTuning({ transcription_backend: "whisper" });
    expect(screen.queryByText("Longest word (Parakeet only)")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton", { name: "Longest word (seconds)" })).not.toBeInTheDocument();

    view.rerender(
      <ProcessingTuningSettings settings={{ transcription_backend: "parakeet" }} onUpdate={onUpdate} />,
    );

    expect(screen.getByText("Longest word (Parakeet only)")).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Longest word (seconds)" })).toHaveAttribute(
      "placeholder",
      "Default (0.4)",
    );
  });

  it("keeps the old internal names off the card", () => {
    renderTuning({ transcription_backend: "parakeet" });

    for (const old of [/phantom/i, /flip/i, /similarity/i, /floor/i, /length cap/i, /segments/i]) {
      expect(screen.queryByText(old)).not.toBeInTheDocument();
    }
  });

  it.each([
    ["absent", {}],
    ["null", { phantom_filter_off: null }],
    ["false", { phantom_filter_off: false }],
  ])("ticks the brief-speaker check and shows its settings when the switch is %s", (_name, settings) => {
    renderTuning(settings as Settings);

    expect(screen.getByRole("checkbox", { name: "Check speakers heard only briefly" })).toBeChecked();
    expect(screen.getByRole("group", { name: "Speakers heard only briefly" })).toBeVisible();
  });

  it("unticking hides the brief-speaker limits and saves the check as off", () => {
    const state = renderWithState({ phantom_max_segments: 2, phantom_merge_threshold: 0.7 });
    const checkbox = screen.getByRole("checkbox", { name: "Check speakers heard only briefly" });
    expect(checkbox).toHaveAttribute(
      "aria-controls",
      screen.getByRole("group", { name: "Speakers heard only briefly" }).id,
    );

    fireEvent.click(checkbox);

    expect(checkbox).not.toBeChecked();
    expect(screen.queryByRole("group", { name: "Speakers heard only briefly" })).toBeNull();
    expect(screen.queryByRole("spinbutton", { name: "And up to (turns)" })).toBeNull();
    // The other rows stay.
    expect(screen.getByRole("group", { name: "Stray one-word speaker changes" })).toBeVisible();
    expect(state.latest().phantom_filter_off).toBe(true);
    expect(state.latest().phantom_max_segments).toBe(2);
    expect(state.latest().phantom_merge_threshold).toBe(0.7);
  });

  it("ticking again shows the brief-speaker limits with their values and clears the switch", () => {
    const state = renderWithState({ phantom_filter_off: true, phantom_max_segments: 2 });
    const checkbox = screen.getByRole("checkbox", { name: "Check speakers heard only briefly" });
    expect(checkbox).not.toBeChecked();
    expect(screen.queryByRole("spinbutton", { name: "And up to (turns)" })).toBeNull();

    fireEvent.click(checkbox);

    expect(checkbox).toBeChecked();
    expect(screen.getByRole("spinbutton", { name: "And up to (turns)" })).toHaveValue(2);
    expect(state.latest().phantom_filter_off).toBeNull();
  });

  it("describes each field with its row's help text", () => {
    renderTuning({});

    expect(
      screen.getByRole("spinbutton", { name: "Speech detection threshold" }),
    ).toHaveAccessibleDescription(/^Lower keeps quiet or distant speech/);
    expect(screen.getByRole("spinbutton", { name: "Merge from (voice match)" })).toHaveAccessibleDescription(
      /^Someone heard for less than both limits is compared/,
    );
  });

  it("keeps text the browser cannot parse out of the settings", () => {
    const state = renderWithState({ vad_threshold: 0.3 });
    const input = screen.getByRole("spinbutton", { name: "Speech detection threshold" });

    typeUnparseable(input);

    expect(state.latest().vad_threshold).toBe(0.3);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/^Speech detection threshold must be a number\. Lower keeps/);
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

  it.each([
    ["Speech detection threshold", "ArrowUp", "vad_threshold", 0.55],
    ["Merge from (voice match)", "ArrowDown", "phantom_merge_threshold", 0.55],
    ["And up to (turns)", "ArrowUp", "phantom_max_segments", 4],
  ] as const)("steps an empty %s from its default with %s", (name, key, setting, expected) => {
    const state = renderWithState({});
    const input = screen.getByRole("spinbutton", { name });

    const notPrevented = fireEvent.keyDown(input, { key });

    expect(notPrevented).toBe(false);
    expect(state.latest()[setting]).toBe(expected);
    expect(input).toHaveValue(expected);
  });

  it("leaves arrow keys on a filled field to the browser", () => {
    const state = renderWithState({ vad_threshold: 0.3 });
    const input = screen.getByRole("spinbutton", { name: "Speech detection threshold" });

    expect(fireEvent.keyDown(input, { key: "ArrowUp" })).toBe(true);
    expect(state.latest().vad_threshold).toBe(0.3);
  });

  it.each([
    ["unparseable text over a stored value", { vad_threshold: 0.3 }, typeUnparseable],
    [
      "out-of-range text over an empty field",
      { vad_threshold: null },
      (input: HTMLElement) => fireEvent.change(input, { target: { value: "0.95" } }),
    ],
  ] as const)("clears %s on reset", (_case, initial, typeBadText) => {
    const state = renderWithState({ ...initial });
    const field = () => screen.getByRole("spinbutton", { name: "Speech detection threshold" });
    typeBadText(field());
    expect(field()).toHaveAttribute("aria-invalid", "true");

    fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));

    expect(state.latest().vad_threshold).toBeNull();
    expect(field()).not.toHaveAttribute("aria-invalid");
    expect(field()).toHaveValue(null);
  });
});
