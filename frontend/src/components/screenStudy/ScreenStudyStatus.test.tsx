import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useScreenStudyStore } from "@/lib/capture/screenStudy/store";

import ScreenStudyStatus from "./ScreenStudyStatus";

const stopScreenStudy = vi.fn();
const deleteScreenStudyFrames = vi.fn();
const addNotification = vi.fn();

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ stopScreenStudy, deleteScreenStudyFrames }),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

describe("ScreenStudyStatus", () => {
  beforeEach(() => {
    stopScreenStudy.mockReset();
    deleteScreenStudyFrames.mockReset();
    addNotification.mockReset();
    useScreenStudyStore.getState().reset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows nothing for a meeting without a study", () => {
    const { container } = render(<ScreenStudyStatus recordingId="rec-1" />);
    expect(container).toBeEmptyDOMElement();

    useScreenStudyStore.getState().update({ recordingId: "rec-2", status: "sampling" });
    render(<ScreenStudyStatus recordingId="rec-1" />);
    expect(screen.queryByRole("group", { name: "Screen study" })).not.toBeInTheDocument();
  });

  it("shows the frame count, the size and the deletion date while sampling", () => {
    useScreenStudyStore.getState().update({
      recordingId: "rec-1",
      status: "sampling",
      frames: 1_234,
      bytes: 45 * 1024 * 1024,
      deleteAfter: "2026-11-08T12:00:00.000Z",
    });

    render(<ScreenStudyStatus recordingId="rec-1" />);

    expect(screen.getByRole("group", { name: "Screen study" })).toHaveTextContent(
      "Screen study: Sampling. 1,234 frames, 45.0 MB, deleted on 8 Nov 2026",
    );
    // Only the state is announced, not the count that changes every second.
    expect(screen.getByRole("status")).toHaveTextContent(/^Screen study: Sampling\.$/);
    expect(screen.getByRole("button", { name: /Stop the screen study/ })).toBeInTheDocument();
  });

  it("Stop ends sampling and leaves the recording to the other controls", () => {
    useScreenStudyStore.getState().update({ recordingId: "rec-1", status: "sampling" });
    render(<ScreenStudyStatus recordingId="rec-1" />);

    fireEvent.click(screen.getByRole("button", { name: /Stop the screen study/ }));

    expect(stopScreenStudy).toHaveBeenCalledTimes(1);
    expect(deleteScreenStudyFrames).not.toHaveBeenCalled();
  });

  it("Delete asks first, then deletes this meeting's frames", async () => {
    useScreenStudyStore.getState().update({ recordingId: "rec-1", status: "stopped", frames: 3 });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<ScreenStudyStatus recordingId="rec-1" />);

    expect(screen.queryByRole("button", { name: /Stop the screen study/ })).not.toBeInTheDocument();
    const remove = screen.getByRole("button", { name: /Delete this meeting's screen study frames/ });
    fireEvent.click(remove);
    expect(deleteScreenStudyFrames).not.toHaveBeenCalled();
    fireEvent.click(remove);

    await waitFor(() => expect(deleteScreenStudyFrames).toHaveBeenCalledTimes(1));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("says why sampling stopped itself", () => {
    useScreenStudyStore.getState().update({
      recordingId: "rec-1",
      status: "stopped",
      frames: 2,
      error: "The screen study has reached its storage limit, so sampling stopped.",
    });
    render(<ScreenStudyStatus recordingId="rec-1" />);
    expect(screen.getByRole("status")).toHaveTextContent(/reached its storage limit/);
  });

  it("shows the deletion once the frames are gone", () => {
    useScreenStudyStore.getState().update({ recordingId: "rec-1", status: "deleted" });
    render(<ScreenStudyStatus recordingId="rec-1" />);
    expect(screen.getByText("This meeting's frames have been deleted.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
