import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@/test/renderWithProviders";
import { useImportAudioStore } from "@/lib/importAudioStore";

const importAudio = vi.fn();

vi.mock("@/lib/api", () => ({
  importAudio: (...args: unknown[]) => importAudio(...args),
  getSupportedAudioFormats: () => [".wav", ".mp3"],
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ pausedRecording: null, runtimeActive: false }),
}));

import ImportAudioButton from "./ImportAudioButton";
import ImportAudioHost from "./ImportAudioHost";

// The real dialog, so these pin Headless UI's focus return as wired here.
// `fireEvent.click` does not move focus, which is how a tap behaves on a
// phone: the dialog has to find the opener from the click, not from focus.
function renderWithOpeners() {
  render(
    <>
      <input aria-label="Search" />
      <ImportAudioButton />
      <ImportAudioButton appearance="labelled" />
      <ImportAudioHost />
    </>,
  );
  return {
    iconOpener: screen.getAllByRole("button", { name: "Import Audio" })[0],
    labelledOpener: screen.getAllByRole("button", { name: "Import Audio" })[1],
  };
}

async function openFrom(opener: HTMLElement) {
  fireEvent.click(opener);
  return screen.findByRole("dialog");
}

describe("ImportAudioHost", () => {
  beforeEach(() => {
    importAudio.mockReset();
    useImportAudioStore.setState({ isImportAudioOpen: false });
  });

  it("returns focus to the icon opener when the dialog is dismissed with Escape", async () => {
    const { iconOpener } = renderWithOpeners();
    // Something else held focus before the tap, so a restore to "whatever was
    // focused" would land on the search box instead.
    screen.getByRole("textbox", { name: "Search" }).focus();

    const dialog = await openFrom(iconOpener);
    fireEvent.keyDown(dialog, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(iconOpener).toHaveFocus();
    });
  });

  it("returns focus to the labelled opener when the dialog is cancelled", async () => {
    const { labelledOpener } = renderWithOpeners();

    const dialog = await openFrom(labelledOpener);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(labelledOpener).toHaveFocus();
    });
  });

  it("refreshes the list and returns focus to the opener after a finished import", async () => {
    importAudio.mockResolvedValue({});
    const onUpdated = vi.fn();
    window.addEventListener("recording-updated", onUpdated);
    const { iconOpener } = renderWithOpeners();

    const dialog = await openFrom(iconOpener);
    const fileInput = dialog.ownerDocument.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: { files: [new File(["x"], "standup.wav", { type: "audio/wav" })] },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Import Audio" }));

    await waitFor(
      () => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      },
      { timeout: 3000 },
    );
    expect(importAudio).toHaveBeenCalledWith(
      expect.objectContaining({ name: "standup.wav" }),
      expect.objectContaining({ name: "standup" }),
    );
    expect(onUpdated).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(iconOpener).toHaveFocus();
    });
    window.removeEventListener("recording-updated", onUpdated);
  });
});
