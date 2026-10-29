import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Settings } from "@/types";
import AiAutomaticEnhancementSection from "./AiAutomaticEnhancementSection";

describe("automatic title timing", () => {
  it("shows the live title and summary on after five minutes until the user chooses", () => {
    const persist = vi.fn();
    render(<AiAutomaticEnhancementSection settings={{} as Settings} onPersist={persist} />);
    const select = screen.getByRole("combobox", { name: "Live title and summary" });
    expect(select).toHaveValue("5");
    expect(persist).not.toHaveBeenCalled();
    fireEvent.change(select, { target: { value: "10" } });
    expect(persist).toHaveBeenCalledWith({ live_title_delay_minutes: 10 });
  });

  it("keeps a saved Off rather than the default", () => {
    const persist = vi.fn();
    const settings = { live_title_delay_minutes: 0 } as Settings;
    render(<AiAutomaticEnhancementSection settings={settings} onPersist={persist} />);
    expect(screen.getByRole("combobox", { name: "Live title and summary" })).toHaveValue("0");
  });

  it("shows a saved custom delay and preserves other preferences when turning it off", () => {
    const persist = vi.fn();
    const settings = { live_title_delay_minutes: 3, prefer_short_titles: false } as Settings;
    render(<AiAutomaticEnhancementSection settings={settings} onPersist={persist} />);
    const select = screen.getByRole("combobox", { name: "Live title and summary" });
    expect(select).toHaveValue("3");
    fireEvent.change(select, { target: { value: "0" } });
    expect(persist).toHaveBeenCalledWith({ ...settings, live_title_delay_minutes: 0 });
  });
});
