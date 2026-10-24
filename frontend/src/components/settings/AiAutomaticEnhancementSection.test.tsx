import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Settings } from "@/types";

import AiAutomaticEnhancementSection from "./AiAutomaticEnhancementSection";

const NAME = "Confirm suggested names automatically";

const renderSection = (settings: Settings) => {
  const onPersist = vi.fn();
  render(<AiAutomaticEnhancementSection settings={settings} onPersist={onPersist} />);
  return { onPersist };
};

describe("AiAutomaticEnhancementSection suggested names", () => {
  it("is off until the user turns it on", () => {
    renderSection({});

    expect(screen.getByRole("switch", { name: NAME })).toHaveAttribute("aria-checked", "false");
  });

  it("saves the setting turned on, keeping the others", () => {
    const { onPersist } = renderSection({ prefer_short_titles: false });

    fireEvent.click(screen.getByRole("switch", { name: NAME }));

    expect(onPersist).toHaveBeenCalledWith({
      prefer_short_titles: false,
      confirm_suggested_names: true,
    });
  });

  it("saves it turned off again", () => {
    const { onPersist } = renderSection({ confirm_suggested_names: true });

    fireEvent.click(screen.getByRole("switch", { name: NAME }));

    expect(onPersist).toHaveBeenCalledWith({ confirm_suggested_names: false });
  });
});

const LEARNING = "Learn voiceprints from unconfirmed names";

describe("AiAutomaticEnhancementSection voiceprint learning", () => {
  it("is off, learning only from confirmed names, until turned on", () => {
    renderSection({});

    expect(screen.getByRole("switch", { name: LEARNING })).toHaveAttribute("aria-checked", "false");
  });

  it("saves automatic when turned on and confirmed when turned off", () => {
    const { onPersist } = renderSection({ voiceprint_learning: "automatic" });

    expect(screen.getByRole("switch", { name: LEARNING })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("switch", { name: LEARNING }));

    expect(onPersist).toHaveBeenCalledWith({ voiceprint_learning: "confirmed" });
  });

  it("saves automatic, keeping the other settings", () => {
    const { onPersist } = renderSection({ confirm_suggested_names: true });

    fireEvent.click(screen.getByRole("switch", { name: LEARNING }));

    expect(onPersist).toHaveBeenCalledWith({
      confirm_suggested_names: true,
      voiceprint_learning: "automatic",
    });
  });
});
