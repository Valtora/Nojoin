import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureResumeSupersededError } from "@/lib/capture/shared";

import LiveMeetingControls from "./LiveMeetingControls";

const addNotification = vi.fn();
const pause = vi.fn();
const resume = vi.fn();
const stop = vi.fn();
const cancel = vi.fn();
const updateRecordingMaxSpeakers = vi.fn();

const captureState = {
  controller: {
    getState: () => ({ error: null }),
  },
  elapsedSeconds: 12,
  pause: (...args: unknown[]) => pause(...args),
  resume: (...args: unknown[]) => resume(...args),
  runtimeActive: true,
  ownedRecordingId: "rec-1" as string | null,
  pausedRecording: null as { id: string } | null,
  status: "recording",
  recordingId: "rec-1" as string | null,
  stop: (...args: unknown[]) => stop(...args),
  cancel: (...args: unknown[]) => cancel(...args),
  coverageWarning: null as {
    capturedSeconds: number;
    elapsedSeconds: number;
    missingSeconds: number;
    queuedSeconds: number;
    cause: string;
  } | null,
  dismissCoverageWarning: vi.fn(),
  stopStage: null as string | null,
  uploadQueue: { segments: 0, seconds: 0 },
};

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => captureState,
  useCaptureState: (select: (state: typeof captureState) => unknown) => select(captureState),
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
    cancel.mockReset();
    captureState.controller.getState = () => ({ error: null });
    captureState.elapsedSeconds = 12;
    captureState.runtimeActive = true;
    captureState.ownedRecordingId = "rec-1";
    captureState.pausedRecording = null;
    captureState.recordingId = "rec-1";
    captureState.status = "recording";
    captureState.coverageWarning = null;
    captureState.stopStage = null;
    captureState.uploadQueue = { segments: 0, seconds: 0 };
  });

  it("says a stop is uploading, not that the meeting is paused", () => {
    // The 2 h meeting behind issue #417 showed "Paused" for the 13 minutes its
    // stop spent sending queued audio.
    captureState.status = "finalizing";
    captureState.stopStage = "flushing-uploads";
    captureState.uploadQueue = { segments: 412, seconds: 824 };

    render(<LiveMeetingControls size="bar" />);

    expect(screen.getByText("Uploading")).toBeInTheDocument();
    expect(screen.queryByText("Paused")).not.toBeInTheDocument();
    expect(screen.getByText(/Uploading the last 13:44 of audio/)).toBeInTheDocument();
  });

  // jsdom has no layout. In a 390px viewport the group held one line and put
  // the speaker cap's increase button 107px past the toolbar card (#538).
  it("lets Attach Docs and the speaker cap wrap on a narrow card", () => {
    render(
      <LiveMeetingControls
        size="bar"
        barTrailing={<button type="button">Attach Docs</button>}
      />,
    );

    const group = screen.getByRole("button", { name: "Attach Docs" }).parentElement;
    expect(group?.contains(screen.getByLabelText(/max speakers/i))).toBe(true);
    expect(group?.className).toContain("flex-wrap");
    expect(group?.className).not.toContain("shrink-0");
  });

  // At 360px a fixed 18rem floor put Stop and Discard 10px past the card.
  it("never holds the transport wider than the card", () => {
    render(<LiveMeetingControls size="bar" />);

    const transport = screen.getByTitle("Stop recording").parentElement;
    expect(transport?.className).toContain("min-w-[min(18rem,100%)]");
  });

  // The page's copy of the recording catches up with a pause on its next poll;
  // the capturing tab must not follow it meanwhile.
  it("keeps the capturing tab's own state and clock ahead of the server", () => {
    captureState.status = "paused";

    render(<LiveMeetingControls size="bar" serverPaused={false} />);

    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.queryByText("Recording")).not.toBeInTheDocument();
    expect(screen.getByText("00:12")).toBeInTheDocument();
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
  });

  it("says a stop is finishing once nothing is left to send", () => {
    captureState.status = "finalizing";
    captureState.stopStage = "finalizing";

    render(<LiveMeetingControls size="bar" />);

    expect(screen.getByText("Finishing")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("warns once uploads fall behind the recording", () => {
    captureState.uploadQueue = { segments: 20, seconds: 40 };

    render(<LiveMeetingControls size="full" />);

    expect(screen.getByText(/Uploads are 00:40 behind/)).toBeInTheDocument();
  });

  it("stays quiet about ordinary upload latency", () => {
    captureState.uploadQueue = { segments: 2, seconds: 4 };

    render(<LiveMeetingControls size="full" />);

    expect(screen.queryByText(/behind/)).not.toBeInTheDocument();
  });

  it("renders the captured figure as a clock, not as a float", () => {
    // The server sums segment durations in milliseconds, so this arrives
    // fractional and `seconds % 60` rendered "50:11.099999999999909".
    captureState.coverageWarning = {
      capturedSeconds: 3011.099999999999909,
      elapsedSeconds: 3153,
      missingSeconds: 142,
      queuedSeconds: 0,
      cause: "unknown",
    };

    render(<LiveMeetingControls size="full" />);

    expect(screen.getByText("50:11 captured")).toBeInTheDocument();
    expect(screen.queryByText(/\.\d/)).not.toBeInTheDocument();
  });

  it("says what is queued rather than letting it read as lost", () => {
    captureState.coverageWarning = {
      capturedSeconds: 3011,
      elapsedSeconds: 3153,
      missingSeconds: 20,
      queuedSeconds: 122,
      cause: "backend-unreachable",
    };

    render(<LiveMeetingControls size="full" />);

    expect(
      screen.getByText(/Another 02:02 is recorded and waiting to upload/),
    ).toBeInTheDocument();
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

  it("stays quiet about a Resume that a Stop or Discard overtook", async () => {
    captureState.status = "paused";
    resume.mockRejectedValueOnce(new CaptureResumeSupersededError());
    resume.mockRejectedValueOnce(new Error("Resume failed"));

    render(<LiveMeetingControls size="full" />);

    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(resume).toHaveBeenCalledOnce());
    // A Resume that failed on its own still toasts, and only that one.
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: "Resume failed",
      });
    });
    expect(addNotification).toHaveBeenCalledOnce();
  });

  it.each(["compact", "full", "bar"] as const)(
    "%s never allows a stored watcher ID to enable Stop or Discard", (size) => {
      captureState.runtimeActive = false;
      captureState.ownedRecordingId = null;
      captureState.status = "paused";
      // A recovery offer for a different recording is not permission for this ID.
      captureState.pausedRecording = { id: "rec-other" };
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
      render(<LiveMeetingControls size={size} />);

      const stopButton = screen.getByTitle("Stop recording");
      const discardButton = screen.getByTitle("Discard recording");
      expect(stopButton).toBeDisabled();
      expect(discardButton).toBeDisabled();
      fireEvent.click(stopButton);
      fireEvent.click(discardButton);
      expect(stop).not.toHaveBeenCalled();
      expect(cancel).not.toHaveBeenCalled();
      expect(confirm).not.toHaveBeenCalled();
      confirm.mockRestore();
    },
  );

  it.each(["owner after a failed stop", "authorised recovery"])(
    "keeps Stop and Discard usable for %s without live tracks", async (kind) => {
      captureState.runtimeActive = false;
      captureState.status = "paused";
      captureState.ownedRecordingId = kind === "owner after a failed stop" ? "rec-1" : null;
      captureState.pausedRecording = kind === "authorised recovery" ? { id: "rec-1" } : null;
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
      stop.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
      cancel.mockResolvedValue(undefined);
      render(<LiveMeetingControls size="bar" />);

      expect(screen.getByTitle("Resume recording")).toBeDisabled();
      expect(screen.getByTitle("Stop recording")).toBeEnabled();
      expect(screen.getByTitle("Discard recording")).toBeEnabled();
      fireEvent.click(screen.getByTitle("Stop recording"));
      await waitFor(() => expect(stop).toHaveBeenCalledOnce());
      fireEvent.click(screen.getByTitle("Discard recording"));
      await waitFor(() => expect(cancel).toHaveBeenCalledOnce());
      confirm.mockRestore();
    },
  );
});

describe("LiveMeetingControls speaker cap", () => {
  beforeEach(() => {
    addNotification.mockReset();
    updateRecordingMaxSpeakers.mockReset();
    captureState.controller.getState = () => ({ error: null });
    captureState.runtimeActive = true;
    captureState.ownedRecordingId = "rec-1";
    captureState.pausedRecording = null;
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
