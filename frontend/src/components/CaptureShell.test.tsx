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

let pathname = "/";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => pathname,
}));

vi.mock("@/lib/capture/usePausedRecordingGuard", () => ({
  usePausedRecordingGuard: () => guardState,
}));

let captureStatus = "idle";
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel,
    controller: { getState: () => ({ error: null }) },
    resume,
    runtimeActive: false,
    status: captureStatus,
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
    captureStatus = "idle";
    pathname = "/";
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

  it("renders an already-offered paused recording as paused, with Resume", () => {
    // Which recordings are offered is discovery's decision, covered with the
    // real provider in CaptureShell.pausedWatcher.test.tsx; this gives the
    // dialog an offer directly.
    guardState.pausedRecording = { ...interrupted, status: "PAUSED", client_status: "PAUSED" };
    render(<CaptureShell><div /></CaptureShell>);
    expect(screen.getByText("Recording paused")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume recording" })).toBeEnabled();
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

  it("asks what to do before anything new, except on the recording's own page", () => {
    const { rerender } = render(<CaptureShell><div /></CaptureShell>);
    expect(screen.getByRole("heading", { name: "Choose what to do before starting anything new" }))
      .toBeInTheDocument();

    pathname = "/recordings/rec-lost";
    rerender(<CaptureShell><div /></CaptureShell>);
    expect(screen.getByRole("heading", { name: "Choose what to do with this recording" }))
      .toBeInTheDocument();
  });

  it("stays shut over a Resume this tab started from the page, and opens if it fails", () => {
    // The recording's own Resume, after a reload, while the share picker is open.
    captureStatus = "starting";
    const { rerender } = render(<CaptureShell><div /></CaptureShell>);
    expect(screen.queryByRole("button", { name: "Resume recording" })).not.toBeInTheDocument();

    captureStatus = "error";
    rerender(<CaptureShell><div /></CaptureShell>);
    expect(screen.getByRole("button", { name: "Resume recording" })).toBeEnabled();
  });
});
