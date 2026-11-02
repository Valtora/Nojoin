import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getLanguageOptions, getRecording, getSettings, reprocessRecording } from "@/lib/api";
import type { LanguageRegistry, Recording } from "@/types";

import { act, fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";

const addNotification = vi.hoisted(() => vi.fn());
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  getLanguageOptions: vi.fn(),
  getRecording: vi.fn(),
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

beforeEach(() => {
  vi.mocked(getSettings).mockResolvedValue({});
});

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

describe("ReprocessDialog Whisper model size", () => {
  const props = { recordingId: "rec-1", onClose: vi.fn() };

  const modelSize = () =>
    screen.getByLabelText("Whisper Model Size") as HTMLSelectElement;

  it("opens on the installation's model size and sends it", async () => {
    vi.mocked(getSettings).mockResolvedValue({ whisper_model_size: "base" });
    await openDialog(null);

    await vi.waitFor(() => expect(modelSize().value).toBe("base"));
    expect(await submit()).toMatchObject({
      transcription_backend: "whisper",
      whisper_model_size: "base",
    });
  });

  it("reopens on the installation's size after another was picked", async () => {
    vi.mocked(getLanguageOptions).mockResolvedValue(registry);
    vi.mocked(getSettings).mockResolvedValue({ whisper_model_size: "small" });
    const { rerender } = renderWithProviders(<ReprocessDialog {...props} isOpen />);
    await vi.waitFor(() => expect(modelSize().value).toBe("small"));
    fireEvent.change(modelSize(), { target: { value: "large" } });
    expect(modelSize().value).toBe("large");

    rerender(<ReprocessDialog {...props} isOpen={false} />);
    vi.mocked(getSettings).mockResolvedValue({ whisper_model_size: "medium" });
    rerender(<ReprocessDialog {...props} isOpen />);

    await vi.waitFor(() => expect(modelSize().value).toBe("medium"));
  });

  it.each([
    ["an unknown size", () => vi.mocked(getSettings).mockResolvedValue({ whisper_model_size: "huge" })],
    ["no settings", () => vi.mocked(getSettings).mockRejectedValue(new Error("offline"))],
  ])("falls back to Turbo for %s", async (_, arrange) => {
    arrange();
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    await openDialog(null);

    await vi.waitFor(() => expect(getSettings).toHaveBeenCalled());
    expect(modelSize().value).toBe("turbo");
    expect(await submit()).toMatchObject({ whisper_model_size: "turbo" });
    quiet.mockRestore();
  });
});

describe("ReprocessDialog for a failed recording", () => {
  const props = { recordingId: "rec-1", isOpen: true, onClose: vi.fn() };
  const REPLACES = /replaces the current transcript/;

  beforeEach(() => {
    vi.mocked(getLanguageOptions).mockResolvedValue(registry);
    vi.mocked(reprocessRecording).mockResolvedValue({ id: "rec-1" } as Recording);
    vi.mocked(getRecording).mockResolvedValue({ id: "rec-1", transcript: { segments: [] } } as unknown as Recording);
  });

  it("offers a retry, with nothing to warn about when no transcript is shown", async () => {
    renderWithProviders(<ReprocessDialog {...props} failed hasTranscript={false} />);

    expect(await screen.findByRole("dialog", { name: "Retry processing" })).toBeInTheDocument();
    expect(screen.queryByText("Reprocess at higher quality")).toBeNull();
    expect(screen.queryByText(REPLACES)).toBeNull();
    expect(screen.getByText(/Processing runs again on this recording's audio/)).toBeInTheDocument();
  });

  it("still warns when a transcript from an earlier run would be replaced", async () => {
    renderWithProviders(<ReprocessDialog {...props} failed hasTranscript />);

    expect(await screen.findByText(REPLACES)).toBeInTheDocument();
  });

  it("finds out for itself, as the rail needs, and warns until it knows", async () => {
    let answer: (recording: Recording) => void = () => {};
    vi.mocked(getRecording).mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    renderWithProviders(<ReprocessDialog {...props} failed />);

    expect(await screen.findByText(REPLACES)).toBeInTheDocument();
    await act(async () => answer({ id: "rec-1", transcript: { segments: [], text: "" } } as unknown as Recording));
    expect(screen.queryByText(REPLACES)).toBeNull();
    expect(screen.getByText(/Processing runs again on this recording's audio/)).toBeInTheDocument();
  });

  it.each([
    ["a failed reprocess kept the transcript", { previous_publication_available: true }],
    ["lines are left from a run", { transcript: { segments: [{ start: 0, end: 1, speaker: "A", text: "Hi" }] } }],
    ["the recording cannot be read", null],
  ])("warns from the rail when %s", async (_, read) => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    if (read === null) {
      vi.mocked(getRecording).mockRejectedValue(new Error("offline"));
    } else {
      vi.mocked(getRecording).mockResolvedValue({ id: "rec-1", ...read } as unknown as Recording);
    }
    renderWithProviders(<ReprocessDialog {...props} failed />);

    await vi.waitFor(() => expect(getRecording).toHaveBeenCalledWith("rec-1"));
    await act(async () => {});
    expect(screen.getByText(REPLACES)).toBeInTheDocument();
    quiet.mockRestore();
  });

  it("keeps the warning for a finished recording", async () => {
    renderWithProviders(<ReprocessDialog {...props} />);

    expect(await screen.findByRole("dialog", { name: "Reprocess at higher quality" })).toBeInTheDocument();
    expect(screen.getByText(REPLACES)).toBeInTheDocument();
  });

  it("sends the same request, then announces the retry for the rail and the page", async () => {
    const updated = vi.fn();
    window.addEventListener("recording-updated", updated);
    renderWithProviders(<ReprocessDialog {...props} failed />);
    await screen.findByLabelText("Language");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await vi.waitFor(() => expect(props.onClose).toHaveBeenCalled());
    expect(reprocessRecording).toHaveBeenCalledWith("rec-1", expect.objectContaining({
      transcription_backend: "whisper",
    }));
    expect(addNotification).toHaveBeenCalledWith({ type: "success", message: "Processing started again." });
    expect((updated.mock.calls[0][0] as CustomEvent).detail).toEqual({ id: "rec-1" });
    window.removeEventListener("recording-updated", updated);
  });
});
