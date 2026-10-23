import { render, waitFor } from "@testing-library/react";
import { AxiosError } from "axios";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { NewNotification } from "@/lib/notificationStore";
import type { Recording } from "@/types";

import UnfinishedImportNotice from "./UnfinishedImportNotice";

const mocks = vi.hoisted(() => ({
  addNotification: vi.fn(),
  getStalledImports: vi.fn(),
  getRecording: vi.fn(),
  discardRecordingCapture: vi.fn(),
  openImportAudio: vi.fn(),
  dispatchRecordingRemoved: vi.fn(),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: { getState: () => ({ addNotification: mocks.addNotification }) },
}));

vi.mock("@/lib/api", () => ({
  getStalledImports: mocks.getStalledImports,
  getRecording: mocks.getRecording,
  discardRecordingCapture: mocks.discardRecordingCapture,
}));

vi.mock("@/lib/importAudioStore", () => ({
  useImportAudioStore: { getState: () => ({ openImportAudio: mocks.openImportAudio }) },
}));

vi.mock("@/lib/recordingEvents", () => ({
  dispatchRecordingRemoved: mocks.dispatchRecordingRemoved,
}));

const stalled = (id: string, name: string) =>
  ({ id, name, status: "UPLOADING", import_stalled: true }) as unknown as Recording;

const notFound = () => {
  const error = new AxiosError("Request failed with status code 404");
  Object.assign(error, { response: { status: 404, data: { detail: "Recording not found" } } });
  return error;
};

const offers = () =>
  mocks.addNotification.mock.calls
    .map(([notification]) => notification as NewNotification)
    .filter((notification) => notification.id?.startsWith("unfinished-import-"));

const action = (notification: NewNotification, label: string) => {
  const found = notification.actions?.find((candidate) => candidate.label === label);
  if (!found) throw new Error(`No ${label} action`);
  return found;
};

const shown = async () => {
  render(<UnfinishedImportNotice />);
  await waitFor(() => expect(offers()).toHaveLength(1));
  return offers()[0];
};

describe("UnfinishedImportNotice (issue #569)", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    mocks.getStalledImports.mockResolvedValue([stalled("rec-1", "Quarterly review")]);
    mocks.getRecording.mockResolvedValue(stalled("rec-1", "Quarterly review"));
    mocks.discardRecordingCapture.mockResolvedValue(undefined);
  });

  it("offers each stopped import once, with a choice to import again or discard", async () => {
    mocks.getStalledImports.mockResolvedValue([
      stalled("rec-1", "Quarterly review"),
      stalled("rec-2", "Board call"),
    ]);

    render(
      <StrictMode>
        <UnfinishedImportNotice />
      </StrictMode>,
    );

    await waitFor(() => expect(offers()).toHaveLength(2));
    expect(mocks.getStalledImports).toHaveBeenCalledTimes(1);
    expect(offers()[0]).toMatchObject({
      id: "unfinished-import-rec-1",
      type: "warning",
      persistent: true,
    });
    expect(offers()[0].message).toContain('"Quarterly review" did not finish');
    expect(offers()[0].actions?.map((entry) => entry.label)).toEqual(["Import again", "Discard"]);
  });

  it("offers nothing when no import stopped", async () => {
    mocks.getStalledImports.mockResolvedValue([]);

    render(<UnfinishedImportNotice />);

    await waitFor(() => expect(mocks.getStalledImports).toHaveBeenCalled());
    expect(offers()).toEqual([]);
  });

  it("discards it on Discard", async () => {
    const offer = await shown();

    action(offer, "Discard").onClick();

    await waitFor(() =>
      expect(mocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1", "import_abandoned"),
    );
    expect(mocks.dispatchRecordingRemoved).toHaveBeenCalledWith("rec-1");
    expect(mocks.openImportAudio).not.toHaveBeenCalled();
  });

  it("discards it and opens Import Audio on Import again", async () => {
    const offer = await shown();

    action(offer, "Import again").onClick();

    await waitFor(() => expect(mocks.openImportAudio).toHaveBeenCalled());
    expect(mocks.discardRecordingCapture).toHaveBeenCalledWith("rec-1", "import_abandoned");
  });

  it("leaves an import alone that is no longer stopped", async () => {
    // A slow part arrived after all, and the import went on to be queued.
    const offer = await shown();
    mocks.getRecording.mockResolvedValue({ id: "rec-1", status: "QUEUED", import_stalled: false });

    action(offer, "Import again").onClick();

    await waitFor(() =>
      expect(mocks.addNotification).toHaveBeenLastCalledWith(
        expect.objectContaining({ type: "info" }),
      ),
    );
    expect(mocks.discardRecordingCapture).not.toHaveBeenCalled();
    expect(mocks.openImportAudio).not.toHaveBeenCalled();
  });

  it("opens Import Audio when the import is gone already", async () => {
    const offer = await shown();
    mocks.getRecording.mockRejectedValue(notFound());

    action(offer, "Import again").onClick();

    await waitFor(() => expect(mocks.openImportAudio).toHaveBeenCalled());
    expect(mocks.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("says so when the discard fails, without opening Import Audio", async () => {
    const offer = await shown();
    mocks.discardRecordingCapture.mockRejectedValue(new AxiosError("Network Error", "ERR_NETWORK"));

    action(offer, "Import again").onClick();

    await waitFor(() =>
      expect(mocks.addNotification).toHaveBeenLastCalledWith(
        expect.objectContaining({ type: "error" }),
      ),
    );
    expect(mocks.openImportAudio).not.toHaveBeenCalled();
  });
});
