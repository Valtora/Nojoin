import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LiveMeetingControls from "./LiveMeetingControls";

const addNotification = vi.fn();
const pause = vi.fn();
const resume = vi.fn();
const stop = vi.fn();

const captureState = {
  controller: {
    getState: () => ({ error: null }),
  },
  elapsedSeconds: 12,
  pause: (...args: unknown[]) => pause(...args),
  resume: (...args: unknown[]) => resume(...args),
  runtimeActive: true,
  status: "recording",
  stop: (...args: unknown[]) => stop(...args),
};

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => captureState,
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

describe("LiveMeetingControls", () => {
  beforeEach(() => {
    addNotification.mockReset();
    pause.mockReset();
    resume.mockReset();
    stop.mockReset();
    captureState.controller.getState = () => ({ error: null });
    captureState.elapsedSeconds = 12;
    captureState.runtimeActive = true;
    captureState.status = "recording";
  });

  it("toasts pause failures instead of rendering them inline", async () => {
    pause.mockRejectedValue(new Error("Pause failed"));

    render(<LiveMeetingControls size="full" />);

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Pause failed",
      });
    });

    expect(screen.queryByText("Pause failed")).not.toBeInTheDocument();
  });
});
