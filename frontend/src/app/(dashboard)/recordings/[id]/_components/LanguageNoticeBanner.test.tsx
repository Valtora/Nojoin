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
  reprocess_language: "auto",
};

const renderBanner = (notice: LanguageNotice | null) =>
  renderWithProviders(
    <LanguageNoticeBanner
      recording={{ id: "rec-1", language_notice: notice } as Recording}
    />,
  );

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

    // Hebrew can't be set, so it reprocesses on auto, with the engine that heard it.
    expect(actions.reprocessInLanguage).toHaveBeenCalledWith(
      "rec-1",
      "auto",
      "whisper",
      expect.anything(),
    );
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
