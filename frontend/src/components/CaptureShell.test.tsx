import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CaptureShell from "./CaptureShell";

const routerPush = vi.fn();
const stop = vi.fn();
const resume = vi.fn();
const cancel = vi.fn();

const guardState = {
  pausedRecording: null as Record<string, unknown> | null,
  hasPausedRecording: false,
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock("@/lib/capture/usePausedRecordingGuard", () => ({
  usePausedRecordingGuard: () => guardState,
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel,
    controller: { getState: () => ({ error: null }) },
    resume,
    runtimeActive: false,
    stop,
    support: { supported: true, mode: "shared_audio" },
  }),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

vi.mock("./SessionSignInDialog", () => ({
  default: () => null,
}));

// A capture whose tab closed while signed out, so its pause never arrived: the
// server still lists it as uploading and has judged it stalled (issue #519).
const interrupted = {
  id: "rec-lost",
  name: "Weekly sync",
  status: "UPLOADING",
  client_status: "RECORDING",
  capture_stalled: true,
};

describe("CaptureShell", () => {
  beforeEach(() => {
    routerPush.mockReset();
    stop.mockReset();
    stop.mockResolvedValue({ id: "rec-lost", status: "QUEUED" });
    resume.mockReset();
    resume.mockResolvedValue({ recordingId: "rec-lost", resumed: true });
    cancel.mockReset();
    guardState.pausedRecording = interrupted;
    guardState.hasPausedRecording = true;
  });

  it("explains an interrupted capture rather than calling it paused", () => {
    render(
      <CaptureShell>
        <div />
      </CaptureShell>,
    );

    expect(screen.getByText("Recording interrupted")).toBeInTheDocument();
    expect(screen.queryByText("Recording paused")).not.toBeInTheDocument();
    expect(
      screen.getByText(/no audio from this recording for over two minutes/),
    ).toBeInTheDocument();
  });

  it("stops the recording it offers, by its own id", async () => {
    render(
      <CaptureShell>
        <div />
      </CaptureShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Stop and process" }));

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/recordings/rec-lost"));
    expect(stop).toHaveBeenCalledWith("rec-lost");
  });

  it("resumes the recording it offers", async () => {
    render(
      <CaptureShell>
        <div />
      </CaptureShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Resume recording" }));

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/recordings/rec-lost"));
    expect(resume).toHaveBeenCalledWith("rec-lost");
  });
});
