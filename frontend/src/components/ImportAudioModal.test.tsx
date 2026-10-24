import { afterEach, describe, expect, it, vi } from "vitest";

import { getLanguageOptions, getSettings, importAudio } from "@/lib/api";
import type { LanguageRegistry, Settings } from "@/types";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";

const addNotification = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  importAudio: vi.fn(),
  // Pending unless a test answers them: the language field stays hidden.
  getLanguageOptions: vi.fn(() => new Promise(() => {})),
  getSettings: vi.fn(() => new Promise(() => {})),
}));

import ImportAudioModal from "./ImportAudioModal";

const selectFile = (name: string) => {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("file input not rendered");
  const file = new File(["x"], name, { type: "application/octet-stream" });
  fireEvent.change(input, { target: { files: [file] } });
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("ImportAudioModal", () => {
  it.each([
    "obs-capture.mkv",
    "voice.mka",
    "screen.mov",
    "old.avi",
    "lecture.m4v",
    "stream.ts",
    "camcorder.MTS",
    "dvd.mpg",
    "dvd.mpeg",
    "phone.3gp",
  ])("accepts the media container %s", (name) => {
    renderWithProviders(<ImportAudioModal isOpen onClose={vi.fn()} />);

    selectFile(name);

    expect(addNotification).not.toHaveBeenCalled();
    expect(screen.getByText(name)).toBeInTheDocument();
  });

  it("asks before the page closes while an import uploads (issue #569)", async () => {
    let finish: () => void = () => {};
    vi.mocked(importAudio).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = () => resolve({ id: "rec-1" } as never);
        }),
    );
    renderWithProviders(<ImportAudioModal isOpen onClose={vi.fn()} />);
    selectFile("quarterly-review.mp4");

    const before = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };

    expect(before()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Import Audio" }));
    expect(before()).toBe(true);

    finish();
    await screen.findByText(/success/i);
    expect(before()).toBe(false);
  });

  it("still rejects a format the backend cannot import", () => {
    renderWithProviders(<ImportAudioModal isOpen onClose={vi.fn()} />);

    selectFile("slides.pptx");

    expect(addNotification).toHaveBeenCalledWith(
      expect.objectContaining({ type: "error" }),
    );
    expect(screen.queryByText("slides.pptx")).not.toBeInTheDocument();
  });

  it("imports in the language chosen for the recording", async () => {
    const registry: LanguageRegistry = {
      transcription_languages: [
        { code: "auto", label: "Auto-detect", forced_engines: [] },
        { code: "fr", label: "French", forced_engines: ["whisper", "canary"] },
      ],
      notes_languages: [],
      custom_instruction_max_length: 300,
      engine_capabilities: {
        whisper: { forced_language: true, auto_detect: true, guidance: "" },
        canary: { forced_language: true, auto_detect: false, guidance: "" },
        parakeet: { forced_language: false, auto_detect: true, guidance: "" },
      },
    };
    vi.mocked(getLanguageOptions).mockResolvedValue(registry);
    vi.mocked(getSettings).mockResolvedValue({
      transcription_backend: "whisper",
    } as Settings);
    vi.mocked(importAudio).mockResolvedValue({ id: "rec-1" } as never);
    renderWithProviders(<ImportAudioModal isOpen onClose={vi.fn()} />);
    selectFile("board.mp3");

    fireEvent.change(await screen.findByLabelText("Language"), {
      target: { value: "fr" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import Audio" }));

    await screen.findByText(/success/i);
    expect(vi.mocked(importAudio).mock.calls[0][1]).toMatchObject({
      transcriptionLanguage: "fr",
    });
  });
});
