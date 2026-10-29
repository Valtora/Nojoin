import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MeetingControls from "./MeetingControls";

const routerPush = vi.fn();
const addNotification = vi.fn();
const start = vi.fn();
const updateSettings = vi.fn();
let connectivityStatus = "online";

const captureState = {
  controller: {
    getState: () => ({ error: null }),
  },
  pausedRecording: null as { id: string; status: string } | null,
  runtimeActive: false,
  settings: { startMode: "shared_audio" },
  updateSettings,
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

vi.mock("./LiveMeetingControls", () => ({
  default: () => <div data-testid="live-controls" />,
}));

describe("MeetingControls", () => {
  beforeEach(() => {
    routerPush.mockReset();
    addNotification.mockReset();
    start.mockReset();
    updateSettings.mockReset();
    captureState.settings = { startMode: "shared_audio" };
    connectivityStatus = "online";
    captureState.controller.getState = () => ({ error: null });
    captureState.runtimeActive = false;
    captureState.pausedRecording = null;
    captureState.status = "idle";
    captureState.support = {
      supported: true,
      mode: "shared_audio",
    };
  });

  it.each(["dashboard", "rail"] as const)("toasts %s start failures instead of rendering them inline", async (variant) => {
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

  it.each(["dashboard", "rail"] as const)("does not toast or navigate when the %s share picker is cancelled", async (variant) => {
    start.mockResolvedValue(null);

    render(<MeetingControls variant={variant} />);

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    await waitFor(() => {
      expect(start).toHaveBeenCalledWith("");
    });

    expect(addNotification).not.toHaveBeenCalled();
    expect(routerPush).not.toHaveBeenCalled();
  });

  it.each(["sidebar", "rail"] as const)("starts and opens a recording from the %s control", async (variant) => {
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

  // Saving the choice and starting with it run for real in
  // MeetingCaptureSource.integration.test.tsx; here each surface offers it.
  it.each(["sidebar", "dashboard", "rail"] as const)("chooses a source beside %s Start without starting early", (variant) => {
    render(<MeetingControls variant={variant} />);
    const select = screen.getByRole("combobox", { name: "Recording source" });
    expect(select).toHaveValue("shared_audio");
    fireEvent.change(select, { target: { value: "microphone_only" } });
    expect(updateSettings).toHaveBeenCalledExactlyOnceWith({ startMode: "microphone_only" });
    expect(start).not.toHaveBeenCalled();
  });

  it.each(["sidebar", "dashboard", "rail"] as const)("says %s Start shares nothing once Microphone only is chosen", async (variant) => {
    captureState.settings = { startMode: "microphone_only" };
    render(<MeetingControls variant={variant} />);
    const select = screen.getByRole("combobox", { name: "Recording source" });
    expect(select).toHaveValue("microphone_only");
    expect(select).toHaveAttribute("title", "Recording source: Microphone only");
    const button = screen.getByRole("button", { name: "Start Meeting" });
    expect(button).toHaveAttribute("title", "Start a microphone-only recording without sharing a tab or screen.");
    fireEvent.click(button);
    await waitFor(() => expect(start).toHaveBeenCalledExactlyOnceWith(""));
  });

  it.each(["sidebar", "dashboard", "rail"] as const)("keeps phone %s Start microphone-only without a selector", async (variant) => {
    captureState.support.mode = "microphone_only";
    captureState.settings.startMode = "shared_audio";
    render(<MeetingControls variant={variant} />);
    expect(screen.queryByRole("combobox", { name: "Recording source" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
    await waitFor(() => expect(start).toHaveBeenCalledExactlyOnceWith(""));
  });

  describe.each(["sidebar", "rail"] as const)("%s start availability", (variant) => {
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
      const select = screen.queryByRole("combobox", { name: "Recording source" });
      if (unsupported) expect(select).not.toBeInTheDocument();
      else expect(select).toBeDisabled();
      fireEvent.click(button);
      expect(start).not.toHaveBeenCalled();
      expect(routerPush).not.toHaveBeenCalled();
    });
  });

  it.each(["recording", "paused"])("keeps the rail disabled during live %s without rendering live controls", (status) => {
    captureState.runtimeActive = true;
    captureState.status = status;
    const { rerender } = render(<MeetingControls variant="rail" />);

    const button = screen.getByRole("button", { name: "Meeting in progress" });
    expect(button).toBeDisabled();
    expect(screen.queryByRole("combobox", { name: "Recording source" })).not.toBeInTheDocument();
    expect(button).toHaveAttribute(
      "title",
      "A meeting recording is already running. Use the recording controls to pause or stop it.",
    );
    expect(screen.queryByTestId("live-controls")).not.toBeInTheDocument();
    fireEvent.click(button);
    expect(start).not.toHaveBeenCalled();

    rerender(<MeetingControls variant="sidebar" />);
    expect(screen.getByTestId("live-controls")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Meeting in progress" })).not.toBeInTheDocument();
    rerender(<MeetingControls variant="rail" />);
    expect(screen.getByRole("button", { name: "Meeting in progress" })).toBeDisabled();
  });

  it.each(["sidebar", "rail"] as const)("blocks %s start when an errored capture still has a runtime", (variant) => {
    captureState.runtimeActive = true;
    captureState.status = "error";
    render(<MeetingControls variant={variant} />);

    const button = screen.getByRole("button", { name: "Meeting in progress" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(start).not.toHaveBeenCalled();
    expect(routerPush).not.toHaveBeenCalled();
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
      expect(screen.getByTestId("live-controls")).toBeInTheDocument();
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
