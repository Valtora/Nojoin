import { afterEach, describe, expect, it, vi } from "vitest";

import { getLanguageOptions, reprocessRecording } from "@/lib/api";
import type { LanguageRegistry, Recording } from "@/types";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  getLanguageOptions: vi.fn(),
  getSettings: vi.fn(),
  reprocessRecording: vi.fn(),
}));

import ReprocessDialog from "./ReprocessDialog";

const registry: LanguageRegistry = {
  transcription_languages: [
    { code: "auto", label: "Auto-detect", forced_engines: [] },
    { code: "fr", label: "French", forced_engines: ["whisper", "canary"] },
    { code: "en", label: "English", forced_engines: ["whisper", "canary"] },
  ],
  notes_languages: [],
  custom_instruction_max_length: 300,
  engine_capabilities: {
    whisper: { forced_language: true, auto_detect: true, guidance: "" },
    canary: { forced_language: true, auto_detect: false, guidance: "" },
    parakeet: { forced_language: false, auto_detect: true, guidance: "" },
  },
};

const openDialog = async (currentTranscriptionLanguage: string | null) => {
  vi.mocked(getLanguageOptions).mockResolvedValue(registry);
  vi.mocked(reprocessRecording).mockResolvedValue({ id: "rec-1" } as Recording);
  renderWithProviders(
    <ReprocessDialog
      recordingId="rec-1"
      isOpen
      onClose={vi.fn()}
      onReprocessed={vi.fn()}
      currentTranscriptionLanguage={currentTranscriptionLanguage}
    />,
  );
  return (await screen.findByLabelText("Language")) as HTMLSelectElement;
};

const submit = async () => {
  fireEvent.click(screen.getByRole("button", { name: "Reprocess" }));
  await vi.waitFor(() => expect(reprocessRecording).toHaveBeenCalled());
  return vi.mocked(reprocessRecording).mock.calls[0][1];
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("ReprocessDialog language", () => {
  it("opens on the recording's language and sends the one chosen", async () => {
    const select = await openDialog("fr");

    expect(select.value).toBe("fr");
    fireEvent.change(select, { target: { value: "en" } });

    expect(await submit()).toMatchObject({ transcription_language: "en" });
  });

  it("reopens on the language of the recording it now names", async () => {
    vi.mocked(getLanguageOptions).mockResolvedValue(registry);
    const props = {
      recordingId: "rec-1",
      onClose: vi.fn(),
      onReprocessed: vi.fn(),
    };
    const { rerender } = renderWithProviders(
      <ReprocessDialog {...props} isOpen currentTranscriptionLanguage={null} />,
    );
    const first = (await screen.findByLabelText("Language")) as HTMLSelectElement;
    fireEvent.change(first, { target: { value: "en" } });

    rerender(
      <ReprocessDialog {...props} isOpen={false} currentTranscriptionLanguage={null} />,
    );
    rerender(<ReprocessDialog {...props} isOpen currentTranscriptionLanguage="fr" />);

    const reopened = (await screen.findByLabelText("Language")) as HTMLSelectElement;
    expect(reopened.value).toBe("fr");
  });

  it("sends null for Your default, so the recording follows the setting again", async () => {
    const select = await openDialog("fr");

    fireEvent.change(select, { target: { value: "" } });

    const body = await submit();
    expect(body).toHaveProperty("transcription_language", null);
  });

  it("sends no language for Parakeet, so the recording keeps its own", async () => {
    await openDialog("fr");

    fireEvent.change(screen.getByLabelText("Transcription engine"), {
      target: { value: "parakeet" },
    });
    expect(screen.queryByLabelText("Language")).toBeNull();

    expect(await submit()).not.toHaveProperty("transcription_language");
  });
});
