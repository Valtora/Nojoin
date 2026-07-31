import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LiveMeetingControls from "./LiveMeetingControls";

const addNotification = vi.fn();
const pause = vi.fn();
const resume = vi.fn();
const stop = vi.fn();
const updateRecordingMaxSpeakers = vi.fn();

const captureState = {
  controller: {
    getState: () => ({ error: null }),
  },
  elapsedSeconds: 12,
  pause: (...args: unknown[]) => pause(...args),
  resume: (...args: unknown[]) => resume(...args),
  runtimeActive: true,
  status: "recording",
  recordingId: "rec-1" as string | null,
  stop: (...args: unknown[]) => stop(...args),
};

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => captureState,
}));

vi.mock("@/lib/api", () => ({
  updateRecordingMaxSpeakers: (...args: unknown[]) =>
    updateRecordingMaxSpeakers(...args),
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

describe("LiveMeetingControls speaker cap", () => {
  beforeEach(() => {
    addNotification.mockReset();
    updateRecordingMaxSpeakers.mockReset();
    captureState.controller.getState = () => ({ error: null });
    captureState.runtimeActive = true;
    captureState.status = "recording";
    captureState.recordingId = "rec-1";
  });

  it("defaults to auto-detect", () => {
    render(<LiveMeetingControls size="full" />);
    const input = screen.getByLabelText(/max speakers/i) as HTMLInputElement;
    expect(input.value).toBe("");
    expect(updateRecordingMaxSpeakers).not.toHaveBeenCalled();
  });

  it("persists a cap set mid-recording", async () => {
    updateRecordingMaxSpeakers.mockResolvedValue({});
    render(<LiveMeetingControls size="full" />);

    const input = screen.getByLabelText(/max speakers/i);
    fireEvent.change(input, { target: { value: "2" } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(updateRecordingMaxSpeakers).toHaveBeenCalledWith("rec-1", 2);
    });
  });

  it("clears the cap back to auto-detect", async () => {
    updateRecordingMaxSpeakers.mockResolvedValue({});
    render(<LiveMeetingControls size="full" />);

    const input = screen.getByLabelText(/max speakers/i);
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.blur(input);
    await waitFor(() =>
      expect(updateRecordingMaxSpeakers).toHaveBeenCalledWith("rec-1", 3),
    );

    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    await waitFor(() =>
      expect(updateRecordingMaxSpeakers).toHaveBeenCalledWith("rec-1", null),
    );
  });

  it("rolls the field back and toasts when the update fails", async () => {
    updateRecordingMaxSpeakers.mockRejectedValue(new Error("Network down"));
    render(<LiveMeetingControls size="full" />);

    const input = screen.getByLabelText(/max speakers/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "4" } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Network down",
      });
    });
    await waitFor(() => expect(input.value).toBe(""));
  });
});
