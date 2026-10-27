import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Settings } from "@/types";
import LineBreakPauseSettings from "./LineBreakPauseSettings";

describe("LineBreakPauseSettings", () => {
  it("explains which transcripts use the pause in the input's accessible help", () => {
    render(<LineBreakPauseSettings settings={{}} onUpdate={() => {}} />);
    const input = screen.getByRole("spinbutton", { name: "Line break pause (seconds)" });
    expect(input).toHaveAccessibleDescription(/live settling lines/);
    expect(input).toHaveAccessibleDescription(/diarised final transcripts when speakers are detected/);
    expect(input).toHaveAccessibleDescription(/Without speaker detection, this pause does not regroup the transcription engine's segments/);
  });

  it("uses an inherited one-second default and saves a valid pause", () => {
    let latest: Settings = {};
    function Host() {
      const [settings, setSettings] = useState<Settings>({});
      return <LineBreakPauseSettings settings={settings} onUpdate={(next) => {
        latest = next;
        setSettings(next);
      }} />;
    }
    render(<Host />);
    const input = screen.getByRole("spinbutton", { name: "Line break pause (seconds)" });

    expect(input).toHaveAttribute("placeholder", "Default (1)");
    expect(input).toHaveAttribute("min", "0.5");
    expect(input).toHaveAttribute("max", "5");
    fireEvent.change(input, { target: { value: "0.75" } });
    expect(latest.line_break_pause_s).toBe(0.75);

    fireEvent.change(input, { target: { value: "0.4" } });
    expect(latest.line_break_pause_s).toBe(0.75);
    expect(input).toHaveAttribute("aria-invalid", "true");

    fireEvent.change(input, { target: { value: "" } });
    expect(latest.line_break_pause_s).toBeNull();
    expect(input).not.toHaveAttribute("aria-invalid");
  });
});
