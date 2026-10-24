import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import AiLanguageSection from "./AiLanguageSection";
import type { LanguageRegistry, Settings } from "@/types";

// English is not the first forced language here, so a select that falls back
// to its first option can't land on English by accident.
const registry: LanguageRegistry = {
  transcription_languages: [
    { code: "auto", label: "Auto-detect", forced_engines: [] },
    { code: "fr", label: "French", forced_engines: ["whisper", "canary"] },
    { code: "en", label: "English", forced_engines: ["whisper", "canary"] },
  ],
  notes_languages: [],
  custom_instruction_max_length: 300,
  engine_capabilities: {
    whisper: { forced_language: true, auto_detect: true, guidance: "Whisper detects." },
    canary: { forced_language: true, auto_detect: false, guidance: "Canary needs one." },
    parakeet: { forced_language: false, auto_detect: true, guidance: "Parakeet detects." },
  },
};

const renderSection = (settings: Partial<Settings>) => {
  const onPersist = vi.fn();
  render(
    <AiLanguageSection
      settings={settings as Settings}
      onPersist={onPersist}
      languageRegistry={registry}
    />,
  );
  const select = screen.getByRole("combobox") as HTMLSelectElement;
  const labels = Array.from(select.options).map((option) => option.text);
  return { onPersist, select, labels };
};

describe("AiLanguageSection", () => {
  it.each([{ transcription_language: "auto" }, {}])(
    "shows English, not Auto-detect, for Canary on auto (%o)",
    (stored) => {
      const { select, labels } = renderSection({
        transcription_backend: "canary",
        ...stored,
      });

      expect(select.value).toBe("en");
      expect(labels).not.toContain("Auto-detect");
      expect(screen.getByText("Canary needs one.")).toBeTruthy();
    },
  );

  it("keeps a language chosen for Canary", () => {
    const { select, onPersist } = renderSection({
      transcription_backend: "canary",
      transcription_language: "fr",
    });

    expect(select.value).toBe("fr");
    fireEvent.change(select, { target: { value: "en" } });
    expect(onPersist).toHaveBeenCalledWith(
      expect.objectContaining({ transcription_language: "en" }),
    );
  });

  it.each(["whisper", "parakeet"])("offers and shows Auto-detect for %s", (backend) => {
    const { select, labels } = renderSection({
      transcription_backend: backend,
      transcription_language: "auto",
    });

    expect(select.value).toBe("auto");
    expect(labels).toEqual(["Auto-detect", "French", "English"]);
  });
});
