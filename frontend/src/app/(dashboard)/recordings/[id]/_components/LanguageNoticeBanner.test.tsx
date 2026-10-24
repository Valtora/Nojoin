import { afterEach, describe, expect, it, vi } from "vitest";

import type { LanguageNotice, Recording } from "@/types";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";

const actions = {
  setTranscriptionLanguage: vi.fn(),
  reprocessInLanguage: vi.fn(),
};

vi.mock("@/components/recordings/_hooks/useRecordingActions", () => ({
  useRecordingActions: () => actions,
}));

import LanguageNoticeBanner from "./LanguageNoticeBanner";

const hebrew: LanguageNotice = {
  kind: "mismatch",
  detected: "he",
  detected_label: "Hebrew",
  used: "en",
  used_label: "English",
  engine: "whisper",
  model: "large-v3",
  reprocess_language: "auto",
};

const german: LanguageNotice = {
  ...hebrew,
  detected: "de",
  detected_label: "German",
  reprocess_language: "de",
};

const banner = (notice: LanguageNotice | null, onAnswered?: () => void) => (
  <LanguageNoticeBanner
    recording={{ id: "rec-1", language_notice: notice } as Recording}
    onAnswered={onAnswered}
  />
);

const renderBanner = (notice: LanguageNotice | null, onAnswered?: () => void) =>
  renderWithProviders(banner(notice, onAnswered));

afterEach(() => {
  vi.clearAllMocks();
});

describe("LanguageNoticeBanner", () => {
  it("says what detection heard and what the transcript used", () => {
    renderBanner(hebrew);

    expect(
      screen.getByText(
        "Language detection heard Hebrew, but this transcript is in English.",
      ),
    ).toBeTruthy();
  });

  it("is absent when the server sends no notice", () => {
    renderBanner(null);

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("keeps the transcript's language by setting it on the recording", () => {
    renderBanner(hebrew);

    fireEvent.click(screen.getByRole("button", { name: "Keep English" }));

    expect(actions.setTranscriptionLanguage).toHaveBeenCalledWith(
      "rec-1",
      "en",
      expect.anything(),
    );
    expect(actions.reprocessInLanguage).not.toHaveBeenCalled();
  });

  it("reprocesses in the language it names only after a confirm", () => {
    renderBanner(hebrew);

    fireEvent.click(screen.getByRole("button", { name: "Reprocess in Hebrew" }));
    expect(actions.reprocessInLanguage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reprocess" }));

    // The notice itself: its language, and the engine and model that heard it.
    expect(actions.reprocessInLanguage).toHaveBeenCalledWith(
      "rec-1",
      hebrew,
      expect.anything(),
    );
  });

  it("says an unlisted language is reprocessed on Auto-detect", () => {
    renderBanner(hebrew);

    fireEvent.click(screen.getByRole("button", { name: "Reprocess in Hebrew" }));

    expect(
      screen.getByText(/Hebrew isn't in the language list, so this reprocesses on Auto-detect/),
    ).toBeTruthy();
  });

  it("says nothing of Auto-detect for a listed language", () => {
    renderBanner(german);

    fireEvent.click(screen.getByRole("button", { name: "Reprocess in German" }));

    expect(screen.getByText(/^Reprocessing replaces the transcript/)).toBeTruthy();
    expect(screen.queryByText(/Auto-detect/)).toBeNull();
  });

  it("announces only the message, not the buttons", () => {
    renderBanner(hebrew);

    const status = screen.getByRole("status");
    expect(status.textContent).toBe(
      "Language detection heard Hebrew, but this transcript is in English.",
    );
    expect(status.querySelector("button")).toBeNull();
  });

  it("hands focus to the page once an answer hides the notice", () => {
    actions.setTranscriptionLanguage.mockImplementation(
      (_id, _language, callbacks) => callbacks.onSuccess(),
    );
    const onAnswered = vi.fn();
    const { rerender } = renderBanner(hebrew, onAnswered);
    const keep = screen.getByRole("button", { name: "Keep English" });
    keep.focus();

    fireEvent.click(keep);
    expect(onAnswered).not.toHaveBeenCalled(); // the notice is still showing

    rerender(banner(null, onAnswered));
    expect(onAnswered).toHaveBeenCalledTimes(1);
  });

  it("leaves focus alone when the notice goes without an answer here", () => {
    const onAnswered = vi.fn();
    const { rerender } = renderBanner(hebrew, onAnswered);

    rerender(banner(null, onAnswered));

    expect(onAnswered).not.toHaveBeenCalled();
  });

  it("tells the page and the list to refresh once an answer is saved", () => {
    const updated = vi.fn();
    window.addEventListener("recording-updated", updated);
    actions.setTranscriptionLanguage.mockImplementation(
      (_id, _language, callbacks) => callbacks.onSuccess(),
    );
    renderBanner(hebrew);

    fireEvent.click(screen.getByRole("button", { name: "Keep English" }));

    expect(updated).toHaveBeenCalledTimes(1);
    expect((updated.mock.calls[0][0] as CustomEvent).detail).toEqual({ id: "rec-1" });
    window.removeEventListener("recording-updated", updated);
  });
});
