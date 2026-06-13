import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MeetingControls from "./MeetingControls";

const routerPush = vi.fn();
const addNotification = vi.fn();
const start = vi.fn();

const captureState = {
  controller: {
    getState: () => ({ error: null }),
  },
  pausedRecording: null,
  runtimeActive: false,
  start: (...args: unknown[]) => start(...args),
  status: "idle",
  support: {
    supported: true,
    mode: "shared_audio",
  },
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: routerPush,
  }),
}));

vi.mock("@/lib/serviceStatusStore", () => ({
  useServiceStatusStore: () => ({
    backend: true,
  }),
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => captureState,
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

describe("MeetingControls", () => {
  beforeEach(() => {
    routerPush.mockReset();
    addNotification.mockReset();
    start.mockReset();
    captureState.controller.getState = () => ({ error: null });
    captureState.runtimeActive = false;
    captureState.pausedRecording = null;
    captureState.status = "idle";
    captureState.support = {
      supported: true,
      mode: "shared_audio",
    };
  });

  it("toasts start failures instead of rendering them inline", async () => {
    start.mockRejectedValue(new Error("Permission denied by user"));

    render(<MeetingControls variant="dashboard" />);

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Permission denied by user",
      });
    });

    expect(screen.queryByText("Permission denied by user")).not.toBeInTheDocument();
    expect(routerPush).not.toHaveBeenCalled();
  });

  it("does not toast or navigate when the share picker is cancelled", async () => {
    start.mockResolvedValue(null);

    render(<MeetingControls variant="dashboard" />);

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    await waitFor(() => {
      expect(start).toHaveBeenCalledWith("");
    });

    expect(addNotification).not.toHaveBeenCalled();
    expect(routerPush).not.toHaveBeenCalled();
  });
});
