import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { getLanguageOptions, getSettings } from "@/lib/api";
import type { LanguageRegistry, Settings } from "@/types";

import RecordingLanguageField, {
  type TranscriptionEngine,
} from "./RecordingLanguageField";

vi.mock("@/lib/api", () => ({
  getLanguageOptions: vi.fn(),
  getSettings: vi.fn(),
}));

// English is not the first forced language, so a select that falls back to its
// first option can't land on English by accident.
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

const renderField = async (
  value: string | null,
  backend: TranscriptionEngine | undefined,
) => {
  vi.mocked(getLanguageOptions).mockResolvedValue(registry);
  const onChange = vi.fn();
  render(
    <RecordingLanguageField value={value} onChange={onChange} backend={backend} />,
  );
  const select = (await screen.findByRole("combobox")) as HTMLSelectElement;
  const labels = Array.from(select.options).map((option) => option.text);
  return { onChange, select, labels };
};

// Lets every pending request settle, so a field that would appear has.
const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 0)));

afterEach(() => {
  vi.clearAllMocks();
});

describe("RecordingLanguageField", () => {
  it("offers the owner's default, Auto-detect and each language on Whisper", async () => {
    const { select, labels, onChange } = await renderField(null, "whisper");

    expect(labels).toEqual(["Your default", "Auto-detect", "French", "English"]);
    expect(select.value).toBe("");
    fireEvent.change(select, { target: { value: "fr" } });
    expect(onChange).toHaveBeenLastCalledWith("fr");
  });

  it("returns to the owner's setting as null", async () => {
    const { select, onChange } = await renderField("fr", "whisper");

    expect(select.value).toBe("fr");
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("leaves out Auto-detect on Canary and shows a stored auto as English", async () => {
    const { select, labels } = await renderField("auto", "canary");

    expect(labels).not.toContain("Auto-detect");
    expect(select.value).toBe("en");
    expect(screen.getByText("Canary needs one.")).toBeTruthy();
  });

  it("is hidden on Parakeet, which takes no language", async () => {
    vi.mocked(getLanguageOptions).mockResolvedValue(registry);
    render(<RecordingLanguageField value={null} onChange={vi.fn()} backend="parakeet" />);

    await settle();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it.each([
    ["canary", false],
    ["parakeet", null],
  ] as const)(
    "reads the owner's engine (%s) when none is given",
    async (backend, offersAuto) => {
      vi.mocked(getSettings).mockResolvedValue({
        transcription_backend: backend,
      } as Settings);
      vi.mocked(getLanguageOptions).mockResolvedValue(registry);
      render(<RecordingLanguageField value={null} onChange={vi.fn()} />);

      await settle();
      expect(getSettings).toHaveBeenCalled();
      if (offersAuto === null) {
        expect(screen.queryByRole("combobox")).toBeNull();
        return;
      }
      const select = (await screen.findByRole("combobox")) as HTMLSelectElement;
      const labels = Array.from(select.options).map((option) => option.text);
      expect(labels.includes("Auto-detect")).toBe(offersAuto);
    },
  );
});
