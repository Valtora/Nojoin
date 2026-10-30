import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MeetingControls from "./MeetingControls";

const routerPush = vi.fn();
const addNotification = vi.fn();
const start = vi.fn();
let connectivityStatus = "online";

const captureState = {
  controller: {
    getState: () => ({ error: null }),
  },
  pausedRecording: null as { id: string; status: string } | null,
  runtimeActive: false,
  uploadQueue: { segments: 0, seconds: 0 },
  coverageWarning: null,
  dismissCoverageWarning: vi.fn(),
  start: (...args: unknown[]) => start(...args),
  refreshPausedRecording: vi.fn(async () => null),
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

vi.mock("@/lib/connectivity/monitor", () => ({
  useConnectivityStore: (selector: (state: { status: string }) => unknown) =>
    selector({ status: connectivityStatus }),
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => captureState,
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("./LiveMeetingControls", async (importOriginal) => ({
  ...await importOriginal<typeof import("./LiveMeetingControls")>(),
  default: () => <div data-testid="live-controls" />,
}));

describe("MeetingControls", () => {
  beforeEach(() => {
    routerPush.mockReset();
    addNotification.mockReset();
    start.mockReset();
    captureState.refreshPausedRecording.mockClear();
    connectivityStatus = "online";
    captureState.controller.getState = () => ({ error: null });
    captureState.runtimeActive = false;
    captureState.pausedRecording = null;
    captureState.status = "idle";
    captureState.uploadQueue = { segments: 0, seconds: 0 };
    captureState.support = {
      supported: true,
      mode: "shared_audio",
    };
  });

  it.each(["dashboard", "rail", "compact"] as const)("toasts %s start failures instead of rendering them inline", async (variant) => {
    start.mockRejectedValue(new Error("Permission denied by user"));

    render(<MeetingControls variant={variant} />);

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

  it.each(["dashboard", "rail", "compact"] as const)("does not toast or navigate when the %s share picker is cancelled", async (variant) => {
    start.mockResolvedValue(null);

    render(<MeetingControls variant={variant} />);

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    await waitFor(() => {
      expect(start).toHaveBeenCalledWith("");
    });

    expect(addNotification).not.toHaveBeenCalled();
    expect(routerPush).not.toHaveBeenCalled();
  });

  it.each(["dashboard", "sidebar", "rail", "compact"] as const)("hands %s Start directly to the controller so its reservation covers preflight", async (variant) => {
    start.mockResolvedValue({ recordingId: "rec-new" });
    render(<MeetingControls variant={variant} />);

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    expect(start).toHaveBeenCalledWith("");
    expect(captureState.refreshPausedRecording).not.toHaveBeenCalled();
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/recordings/rec-new"));
  });

  it.each(["sidebar", "rail", "compact"] as const)("starts and opens a recording from the %s control", async (variant) => {
    const onMeetingEnd = vi.fn();
    start.mockResolvedValue({ recordingId: "rec-new" });
    render(<MeetingControls variant={variant} onMeetingEnd={onMeetingEnd} />);

    const button = screen.getByRole("button", { name: "Start Meeting" });
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute("title", "Start a new meeting recording.");
    fireEvent.click(button);

    await waitFor(() => {
      expect(routerPush).toHaveBeenCalledWith("/recordings/rec-new");
      expect(onMeetingEnd).toHaveBeenCalledOnce();
    });
    expect(start).toHaveBeenCalledExactlyOnceWith("");
    expect(screen.queryByTestId("live-controls")).not.toBeInTheDocument();
  });

  it("keeps Start reserved when expanding the rail during preflight", async () => {
    let finishStart: (result: { recordingId: string }) => void = () => {};
    start.mockImplementation(() => {
      captureState.status = "starting";
      return new Promise((resolve) => { finishStart = resolve; });
    });
    const onMeetingEnd = vi.fn();
    const { rerender } = render(<MeetingControls variant="rail" onMeetingEnd={onMeetingEnd} />);

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
    for (const variant of ["rail", "sidebar"] as const) {
      rerender(<MeetingControls variant={variant} onMeetingEnd={onMeetingEnd} />);
      const button = screen.getByRole("button", { name: "Starting meeting..." });
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(start).toHaveBeenCalledExactlyOnceWith("");
    expect(captureState.refreshPausedRecording).not.toHaveBeenCalled();
    expect(routerPush).not.toHaveBeenCalled();
    expect(onMeetingEnd).not.toHaveBeenCalled();

    finishStart({ recordingId: "rec-new" });
    await waitFor(() => {
      expect(routerPush).toHaveBeenCalledExactlyOnceWith("/recordings/rec-new");
      expect(onMeetingEnd).toHaveBeenCalledOnce();
    });
  });

  describe.each(["sidebar", "rail", "compact"] as const)("%s start availability", (variant) => {
    it.each([
      { state: "paused", label: "Paused recording needs attention", paused: "PAUSED" },
      { state: "interrupted", label: "Interrupted recording needs attention", paused: "UPLOADING" },
      { state: "starting", label: "Starting meeting...", status: "starting" },
      { state: "finalising", label: "Finalizing meeting...", status: "finalizing" },
      { state: "unsupported", label: "Unsupported browser", unsupported: true },
      { state: "offline", label: "Nojoin unavailable", offline: true },
    ])("prevents starting while $state", ({ label, paused, status, unsupported, offline }) => {
      captureState.pausedRecording = paused ? { id: "rec-held", status: paused } : null;
      captureState.status = status ?? "idle";
      captureState.support.supported = !unsupported;
      connectivityStatus = offline ? "offline" : "online";
      render(<MeetingControls variant={variant} />);

      const button = screen.getByRole("button", { name: label });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("title");
      fireEvent.click(button);
      expect(start).not.toHaveBeenCalled();
      expect(routerPush).not.toHaveBeenCalled();
    });
  });

  describe.each(["rail", "compact"] as const)("%s during capture", (variant) => {
    it.each(["recording", "paused"])("shows passive status during live %s", (status) => {
      captureState.runtimeActive = true;
      captureState.status = status;
      const { rerender } = render(<MeetingControls variant={variant} />);

      expect(screen.getByTestId("capture-status")).toHaveTextContent(status === "paused" ? /^Paused$/ : /^Recording$/);
      // The capture's own controls announce its state; this is no second live region.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(screen.queryByTestId("live-controls")).not.toBeInTheDocument();
      expect(start).not.toHaveBeenCalled();

      rerender(<MeetingControls variant="sidebar" />);
      expect(screen.getByTestId("capture-status")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Meeting in progress" })).not.toBeInTheDocument();
      rerender(<MeetingControls variant={variant} />);
      expect(screen.getByTestId("capture-status")).toBeInTheDocument();
    });
  });

  it.each(["sidebar", "dashboard"] as const)("keeps an upload backlog notice under the %s status", (variant) => {
    captureState.runtimeActive = true;
    captureState.status = "recording";
    captureState.uploadQueue = { segments: 4, seconds: 40 };
    render(<MeetingControls variant={variant} />);

    expect(screen.getByTestId("capture-status")).toHaveTextContent(/^Recording$/);
    expect(screen.getByRole("status")).toHaveTextContent("Uploads are 00:40 behind.");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it.each(["sidebar", "rail", "compact"] as const)("blocks %s start when an errored capture still has a runtime", (variant) => {
    captureState.runtimeActive = true;
    captureState.status = "error";
    render(<MeetingControls variant={variant} />);

    const button = screen.getByRole("button", { name: "Meeting in progress" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(start).not.toHaveBeenCalled();
    expect(routerPush).not.toHaveBeenCalled();
  });

  describe.each(["dashboard", "sidebar", "rail"] as const)("%s recovery precedence", (variant) => {
    it.each(["starting", "finalizing"])("preserves the owned %s stage over a retained pause offer", (status) => {
      captureState.runtimeActive = true;
      captureState.status = status;
      captureState.pausedRecording = { id: "rec-owned", status: "PAUSED" };
      render(<MeetingControls variant={variant} />);

      const label = status === "starting" ? "Starting meeting..." : "Finalizing meeting...";
      const button = screen.getByRole("button", { name: label });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(start).not.toHaveBeenCalled();
    });

    it.each([
      { status: "PAUSED", label: "Paused recording needs attention" },
      { status: "UPLOADING", label: "Interrupted recording needs attention" },
    ])("keeps $status attention visible when an errored runtime remains", ({ status, label }) => {
      captureState.runtimeActive = true;
      captureState.status = "error";
      captureState.pausedRecording = { id: "rec-held", status };
      render(<MeetingControls variant={variant} />);

      const button = screen.getByRole("button", { name: label });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute(
        "title",
        "Stop or discard the current recording from its page before starting anything new.",
      );
      expect(screen.queryByRole("button", { name: "Meeting in progress" })).not.toBeInTheDocument();
      expect(screen.queryByTestId("live-controls")).not.toBeInTheDocument();
      fireEvent.click(button);
      expect(start).not.toHaveBeenCalled();
    });
  });

  it("keeps an owned live pause on the rail and expanded recording status", () => {
    captureState.runtimeActive = true;
    captureState.status = "paused";
    captureState.pausedRecording = { id: "rec-owned", status: "PAUSED" };
    const { rerender } = render(<MeetingControls variant="rail" />);

    expect(screen.getByTestId("capture-status")).toHaveTextContent(/^Paused$/);
    for (const variant of ["sidebar", "dashboard"] as const) {
      rerender(<MeetingControls variant={variant} />);
      expect(screen.getByTestId("capture-status")).toHaveTextContent(/^Paused$/);
      expect(screen.queryByRole("button", { name: /needs attention/ })).not.toBeInTheDocument();
    }
    expect(start).not.toHaveBeenCalled();
  });

  describe.each(["sidebar", "dashboard"] as const)("%s capture ownership", (variant) => {
    it.each(["recording", "paused"])("does not render live %s controls without this tab's runtime", (status) => {
      captureState.status = status;
      render(<MeetingControls variant={variant} />);

      expect(screen.queryByTestId("live-controls")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Start Meeting" })).toBeEnabled();
    });
  });

  it("keeps the sidebar's paired action through idle, recording, paused and finalizing", () => {
    const paired = <button type="button">Import Audio</button>;
    const renderAs = (runtimeActive: boolean, status: string) => {
      captureState.runtimeActive = runtimeActive;
      captureState.status = status;
      return <MeetingControls pairedAction={paired} />;
    };
    const { rerender } = render(renderAs(false, "idle"));
    expect(screen.getByRole("button", { name: "Start Meeting" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Import Audio" })).toBeInTheDocument();

    for (const status of ["recording", "paused"]) {
      rerender(renderAs(true, status));
      expect(screen.getByTestId("capture-status")).toHaveTextContent(status === "paused" ? /^Paused$/ : /^Recording$/);
      expect(screen.queryByRole("button", { name: "Start Meeting" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Import Audio" })).toBeInTheDocument();
    }

    // Finalizing hands the row back to the status button, which is disabled
    // and carries the stage; the paired action stays.
    rerender(renderAs(true, "finalizing"));
    expect(screen.queryByTestId("live-controls")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Finalizing meeting..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import Audio" })).toBeInTheDocument();
  });

  it("names an interrupted capture apart from a paused one", () => {
    captureState.pausedRecording = { id: "rec-1", status: "UPLOADING" };
    const { rerender } = render(<MeetingControls variant="dashboard" />);

    expect(
      screen.getByRole("button", { name: "Interrupted recording needs attention" }),
    ).toBeDisabled();

    captureState.pausedRecording = { id: "rec-1", status: "PAUSED" };
    rerender(<MeetingControls variant="dashboard" />);

    expect(
      screen.getByRole("button", { name: "Paused recording needs attention" }),
    ).toBeDisabled();
  });
});
