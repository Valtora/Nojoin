import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RecalibrateModal from "./RecalibrateModal";
import type { GlobalSpeaker, SpeakerSegment } from "@/types";

const addNotification = vi.fn();
const getSpeakerSegments = vi.fn();
const recalibrateSpeaker = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  getSpeakerSegments: (...a: unknown[]) => getSpeakerSegments(...a),
  recalibrateSpeaker: (...a: unknown[]) => recalibrateSpeaker(...a),
  getRecordingStreamUrl: vi.fn(),
  scanMatches: vi.fn(),
  splitSpeaker: vi.fn(),
}));

const segment = (recording_id: string, start: number, end: number): SpeakerSegment => ({
  recording_id,
  recording_name: recording_id,
  recording_date: null,
  start,
  end,
  text: `${recording_id} ${start}`,
});

const renderModal = () =>
  render(
    <RecalibrateModal
      isOpen
      onClose={vi.fn()}
      onComplete={vi.fn()}
      speaker={{ id: 7, name: "Dana" } as GlobalSpeaker}
    />,
  );

describe("RecalibrateModal", () => {
  beforeEach(() => {
    addNotification.mockReset();
    getSpeakerSegments
      .mockReset()
      .mockResolvedValue([
        segment("rec-1", 0, 10),
        segment("rec-1", 20, 32),
        segment("rec-2", 0, 15),
      ]);
    recalibrateSpeaker.mockReset();
  });

  it("counts the speech per recording, not across them", async () => {
    renderModal();

    const approve = await screen.findAllByTitle("Verify / Approve");
    fireEvent.click(approve[2]);
    expect(
      screen.getByText(
        "Up to 15 s from one recording; 20 s of clear speech from one recording needed.",
      ),
    ).toBeTruthy();

    // 10 s more from rec-1 is 25 s selected, but no one recording holds 20 s.
    fireEvent.click(approve[0]);
    expect(screen.getByText(/^Up to 15 s from one recording/)).toBeTruthy();

    fireEvent.click(approve[1]);
    expect(screen.getByText(/^Up to 22 s from one recording/)).toBeTruthy();

    // Toggling a sample off takes its speech back out.
    fireEvent.click(approve[1]);
    expect(screen.getByText(/^Up to 15 s from one recording/)).toBeTruthy();
  });

  it("says when the server recalibrated from only some of the recordings", async () => {
    const partial =
      "Voiceprint recalibrated from 1 of 2 recordings and locked. The others held less than 20 s of clear speech.";
    recalibrateSpeaker.mockResolvedValue({ success: true, message: partial });
    renderModal();

    const approve = await screen.findAllByTitle("Verify / Approve");
    fireEvent.click(approve[0]);
    fireEvent.click(approve[2]);
    fireEvent.click(screen.getByRole("button", { name: /Recalibrate/ }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        type: "success",
        message: `${partial} You can now scan for matches.`,
      }),
    );
  });

  it("sends only the approved samples and shows the server's refusal", async () => {
    recalibrateSpeaker.mockRejectedValue({
      response: {
        status: 400,
        data: {
          detail:
            "No selected recording holds 20 s of clear speech on its own (the most was 15.0 s). Select more of this person's speech from one recording.",
        },
      },
    });
    renderModal();

    const approve = await screen.findAllByTitle("Verify / Approve");
    fireEvent.click(approve[0]);
    fireEvent.click(approve[2]);
    fireEvent.click(screen.getByRole("button", { name: /Recalibrate/ }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message:
          "No selected recording holds 20 s of clear speech on its own (the most was 15.0 s). Select more of this person's speech from one recording.",
      }),
    );
    expect(recalibrateSpeaker).toHaveBeenCalledWith(7, [
      { recording_id: "rec-1", start: 0, end: 10 },
      { recording_id: "rec-2", start: 0, end: 15 },
    ]);
    // A refused recalibration leaves the trainer open, not "Training Complete".
    expect(screen.queryByText("Training Complete")).toBeNull();
  });
});
