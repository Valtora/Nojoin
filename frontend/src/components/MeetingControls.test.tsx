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
  pausedRecording: null as { id: string; status: string } | null,
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

vi.mock("@/lib/connectivity/monitor", () => ({
  useConnectivityStore: (selector: (state: { status: string }) => unknown) =>
    selector({ status: "online" }),
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

const studyAvailability = vi.hoisted(() => ({
  value: null as { rate_hz: number; retention_days: number; max_frame_bytes: number } | null,
}));

vi.mock("@/lib/capture/screenStudy/availability", () => ({
  useScreenStudyAvailability: (enabled: boolean) => (enabled ? studyAvailability.value : null),
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
    studyAvailability.value = null;
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

  describe("screen study opt-in", () => {
    const available = { rate_hz: 2, retention_days: 14, max_frame_bytes: 1_048_576 };

    it("is not offered unless the installation allows it for this account", () => {
      render(<MeetingControls variant="dashboard" />);
      expect(screen.queryByRole("checkbox", { name: /Screen study/ })).not.toBeInTheDocument();
    });

    it("is never offered for a phone's microphone-only capture", () => {
      studyAvailability.value = available;
      captureState.support = { supported: true, mode: "microphone_only" };
      render(<MeetingControls variant="dashboard" />);
      expect(screen.queryByRole("checkbox", { name: /Screen study/ })).not.toBeInTheDocument();
    });

    it.each(["dashboard", "sidebar"] as const)(
      "is offered unticked at Start Meeting (%s)",
      (variant) => {
        studyAvailability.value = available;
        render(<MeetingControls variant={variant} />);
        const box = screen.getByRole("checkbox", { name: /Screen study/ });
        expect(box).not.toBeChecked();
        expect(screen.getByText(/deleted after 14 days|deleted 14 days after/)).toBeInTheDocument();
      },
    );

    it("starts a study meeting only when ticked, at the installation's rate", async () => {
      studyAvailability.value = available;
      start.mockResolvedValue(null);
      render(<MeetingControls variant="dashboard" />);

      fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
      await waitFor(() => expect(start).toHaveBeenCalledWith(""));

      fireEvent.click(screen.getByRole("checkbox", { name: /Screen study/ }));
      fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
      await waitFor(() =>
        expect(start).toHaveBeenLastCalledWith("", { screenStudy: { rateHz: 2 } }),
      );
    });

    it("is unticked again for the next meeting", async () => {
      studyAvailability.value = available;
      start.mockResolvedValue(null);
      render(<MeetingControls variant="sidebar" />);

      fireEvent.click(screen.getByRole("checkbox", { name: /Screen study/ }));
      expect(screen.getByRole("checkbox", { name: /Screen study/ })).toBeChecked();
      fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

      await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
      expect(screen.getByRole("checkbox", { name: /Screen study/ })).not.toBeChecked();
    });

    it("is not offered while a meeting is recording", () => {
      studyAvailability.value = available;
      captureState.runtimeActive = true;
      captureState.status = "recording";
      render(<MeetingControls variant="sidebar" />);
      expect(screen.queryByRole("checkbox", { name: /Screen study/ })).not.toBeInTheDocument();
    });
  });
});
