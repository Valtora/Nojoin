import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureResumeSupersededError } from "@/lib/capture/shared";

import RecordingFloatingBadge from "./RecordingFloatingBadge";

const addNotification = vi.fn();
const resume = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings",
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: vi.fn(),
    controller: { getState: () => ({ error: null }) },
    elapsedSeconds: 12,
    pause: vi.fn(),
    resume: (...args: unknown[]) => resume(...args),
    runtimeActive: true,
    status: "paused",
    stop: vi.fn(),
    recordingId: "rec-1",
  }),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

describe("RecordingFloatingBadge", () => {
  beforeEach(() => {
    addNotification.mockReset();
    resume.mockReset();
  });

  it("stays quiet about a Resume that a Stop or Discard overtook", async () => {
    resume.mockRejectedValueOnce(new CaptureResumeSupersededError());
    resume.mockRejectedValueOnce(new Error("Resume failed"));

    render(<RecordingFloatingBadge />);

    fireEvent.click(screen.getByRole("button", { name: "Resume recording" }));
    await waitFor(() => expect(resume).toHaveBeenCalledOnce());
    // A Resume that failed on its own still toasts, and only that one.
    fireEvent.click(screen.getByRole("button", { name: "Resume recording" }));

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Resume failed",
      });
    });
    expect(addNotification).toHaveBeenCalledOnce();
  });
});
