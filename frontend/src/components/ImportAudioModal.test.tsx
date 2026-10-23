import { afterEach, describe, expect, it, vi } from "vitest";

import { importAudio } from "@/lib/api";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";

const addNotification = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  importAudio: vi.fn(),
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
});
