import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fillMarkerViolations } from "@/test/fillMarkerContracts";

import RecalibrateModal from "./RecalibrateModal";
import type { GlobalSpeaker, SpeakerSegment } from "@/types";
import { selectorFor } from "@/test/depthSelectors";
import { stateSelector, styleRules } from "@/test/styleRules";

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
  afterEach(() => vi.restoreAllMocks());
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

  it("checks pending, rejected, approved and playing fills after loading", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const { unmount } = renderModal();
    const approve = (await screen.findAllByTitle("Verify / Approve"))[0];
    const reject = screen.getAllByTitle("Reject / Not this speaker")[0];
    const card = approve.closest('[data-ui-surface="card"]')!;
    const preview = card.querySelector("button")!;
    expect(preview).toHaveClass("bg-control-bg");
    expect(approve).not.toHaveClass("bg-status-success-bg");
    expect(reject).not.toHaveClass("bg-status-danger-bg");
    expect(fillMarkerViolations(document.body)).toEqual([]);
    fireEvent.click(reject);
    expect(reject).toHaveClass("bg-status-danger-bg");
    expect(fillMarkerViolations(document.body)).toEqual([]);
    fireEvent.click(approve);
    expect(approve).toHaveClass("bg-status-success-bg");
    expect(reject).not.toHaveClass("bg-status-danger-bg");
    expect(fillMarkerViolations(document.body)).toEqual([]);
    fireEvent.click(approve);
    expect(approve).not.toHaveClass("bg-status-success-bg");
    expect(fillMarkerViolations(document.body)).toEqual([]);
    fireEvent.click(preview);
    expect(preview).toHaveClass("bg-status-info-bg");
    expect(fillMarkerViolations(document.body)).toEqual([]);
    fireEvent.click(preview);
    expect(preview).toHaveClass("bg-control-bg");
    expect(fillMarkerViolations(document.body)).toEqual([]);
    unmount();
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

  it("keeps nested card/inset paint local and updates rejected/approved choice contracts", async () => {
    renderModal();
    const approve = (await screen.findAllByTitle("Verify / Approve"))[0];
    const reject = screen.getAllByTitle("Reject / Not this speaker")[0];
    const pane = approve.closest('[data-ui-surface="pane"]')!;
    const footer = pane.closest('[data-ui-surface="inset"]')!;
    const oldStyle = document.documentElement.getAttribute("data-style");
    const oldDark = document.documentElement.classList.contains("dark");
    document.documentElement.dataset.style = "depth";
    document.documentElement.classList.add("dark");
    try {
      const translucent = styleRules("depth-dark", "--depth-fill-base",
        "color-mix(in srgb, var(--surface-inset) 80%, transparent)")[0];
      const card = styleRules("depth-dark", "--depth-fill-base", "var(--surface-card)")[0];
      expect(footer.matches(selectorFor(translucent))).toBe(true);
      expect(pane.matches(selectorFor(card))).toBe(true);
      expect(pane.matches(selectorFor(translucent))).toBe(false);
      const dangerHover = styleRules("depth-dark", "background-color", "var(--status-danger-bg)")[0];
      expect(reject.matches(stateSelector(dangerHover, { hover: true }))).toBe(true);
      expect(reject).not.toHaveAttribute("data-ui-fill");
      fireEvent.click(reject);
      expect(reject).toHaveAttribute("data-ui-fill", "status-danger");
      expect(reject.matches(stateSelector(dangerHover, { hover: true }))).toBe(false);
      fireEvent.click(approve);
      expect(approve).toHaveAttribute("data-ui-fill", "status-success");
      expect(reject).not.toHaveAttribute("data-ui-fill");
      expect(reject.matches(stateSelector(dangerHover, { hover: true }))).toBe(true);
    } finally {
      if (oldStyle === null) document.documentElement.removeAttribute("data-style");
      else document.documentElement.setAttribute("data-style", oldStyle);
      document.documentElement.classList.toggle("dark", oldDark);
    }
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
