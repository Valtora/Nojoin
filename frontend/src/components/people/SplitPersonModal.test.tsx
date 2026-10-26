import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fillMarkerViolations } from "@/test/fillMarkerContracts";

import SplitPersonModal from "./SplitPersonModal";
import type { GlobalSpeaker, SpeakerSegment, SpeakerSplitResult } from "@/types";

const addNotification = vi.fn();
const splitSpeaker = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  getSpeakerSegments: vi.fn(),
  getRecordingStreamUrl: vi.fn(),
  splitSpeaker: (...a: unknown[]) => splitSpeaker(...a),
}));

const dana = { id: 7, name: "Dana" } as GlobalSpeaker;

const segments: SpeakerSegment[] = [
  {
    recording_id: "rec-1",
    recording_name: "Weekly sync",
    recording_date: null,
    start: 0,
    end: 25,
    text: "Robin speaking",
  },
];

const result: SpeakerSplitResult = {
  id: 8,
  name: "Robin",
  has_voiceprint: true,
  is_voiceprint_locked: true,
  library_voiceprint: {
    status: "saved",
    speech_seconds: 25,
    required_speech_seconds: 20,
  },
  original_library_voiceprint: {
    status: "skipped_insufficient_speech",
    speech_seconds: 12,
    required_speech_seconds: 20,
  },
};

describe("SplitPersonModal", () => {
  afterEach(() => vi.restoreAllMocks());
  beforeEach(() => {
    addNotification.mockReset();
    splitSpeaker.mockReset().mockResolvedValue(result);
  });

  it("checks selection and preview fills through their click transitions", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    const { baseElement, unmount } = render(
      <SplitPersonModal isOpen onClose={vi.fn()} onComplete={vi.fn()}
        speaker={dana} initialSegments={segments} />,
    );
    const text = await screen.findByText(/Robin speaking/);
    const card = text.closest('[data-ui-surface="card"]')!;
    const preview = card.querySelector("button")!;
    expect(card).toHaveClass("bg-surface-card");
    expect(preview).toHaveClass("bg-surface-inset");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
    fireEvent.click(text);
    expect(card).toHaveClass("bg-action-tint");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
    fireEvent.click(preview);
    expect(preview).toHaveClass("bg-action");
    expect(card).toHaveClass("bg-action-tint");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
    fireEvent.click(preview);
    expect(preview).toHaveClass("bg-surface-inset");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
    fireEvent.click(text);
    expect(card).toHaveClass("bg-surface-card");
    expect(fillMarkerViolations(baseElement)).toEqual([]);
    unmount();
  });

  it("says what the split did to each voiceprint, not that both were recalibrated", async () => {
    render(
      <SplitPersonModal
        isOpen
        onClose={vi.fn()}
        onComplete={vi.fn()}
        speaker={dana}
        initialSegments={segments}
      />,
    );

    fireEvent.change(await screen.findByPlaceholderText("e.g. Jane Doe"), {
      target: { value: "Robin" },
    });
    fireEvent.click(screen.getByText(/Robin speaking/));
    fireEvent.click(screen.getByRole("button", { name: /Create & Move/ }));

    await waitFor(() =>
      expect(splitSpeaker).toHaveBeenCalledWith(7, "Robin", [
        { recording_id: "rec-1", start: 0, end: 25 },
      ]),
    );
    expect(
      await screen.findByText("Robin's voiceprint was saved from the moved clips."),
    ).toBeTruthy();
    expect(screen.getByText(/^Dana has no voiceprint now:/)).toBeTruthy();
    expect(screen.queryByText(/recalibrated/)).toBeNull();
    // The clips can move to a person who already existed, so nothing says
    // one was created.
    expect(screen.queryByText(/has been created/)).toBeNull();
  });
});
