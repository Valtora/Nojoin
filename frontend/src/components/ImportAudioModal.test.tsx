import { afterEach, describe, expect, it, vi } from "vitest";

import { ImportStillFinalizingError, importAudio } from "@/lib/api";
import {
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
} from "@/test/renderWithProviders";

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

  it("still rejects a format the backend cannot import", () => {
    renderWithProviders(<ImportAudioModal isOpen onClose={vi.fn()} />);

    selectFile("slides.pptx");

    expect(addNotification).toHaveBeenCalledWith(
      expect.objectContaining({ type: "error" }),
    );
    expect(screen.queryByText("slides.pptx")).not.toBeInTheDocument();
  });

  describe("a failed import", () => {
    const importFailing = async (error: unknown) => {
      vi.mocked(importAudio).mockRejectedValueOnce(error);
      renderWithProviders(<ImportAudioModal isOpen onClose={vi.fn()} />);
      selectFile("obs-capture.mkv");
      fireEvent.click(screen.getByRole("button", { name: /Import Audio/ }));
      await waitFor(() => expect(addNotification).toHaveBeenCalled());
      return addNotification.mock.calls[0][0];
    };

    it("shows the message of a structured detail as text", async () => {
      // Finalize answers 409 with {code, message}; the object itself would
      // crash the toast and every later view of the notification history.
      const notification = await importFailing({
        response: {
          status: 409,
          data: {
            detail: {
              code: "import_finalizing",
              message: "This import is already being finalized.",
            },
          },
        },
      });

      expect(notification).toEqual({
        type: "error",
        message: "This import is already being finalized.",
      });
    });

    it("warns, not fails, when the server is still finishing the import", async () => {
      const error = new ImportStillFinalizingError();

      expect(await importFailing(error)).toEqual({
        type: "warning",
        message: error.message,
      });
    });

    it("blames the connection when no answer came back", async () => {
      const notification = await importFailing(new TypeError("Failed to fetch"));

      expect(notification.message).toMatch(/check your connection/);
    });
  });
});
